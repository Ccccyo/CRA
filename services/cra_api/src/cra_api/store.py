from __future__ import annotations

import json
import sqlite3
import warnings
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


STAGES = [
    "intake_excel",
    "resolve_entity",
    "financial_evidence",
    "financial_signoff",
    "retrieve_context",
    "risk_signoff",
    "write_workbook",
    "external_score",
    "final_signoff",
    "completed",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkflowStore:
    def __init__(self, path: Path):
        self.backend = "sqlite"
        self.startup_warning = ""
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with closing(self.connect()) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS workflow_state (
                    case_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    current_stage TEXT NOT NULL,
                    stage_index INTEGER NOT NULL,
                    current_task TEXT NOT NULL,
                    blocker TEXT NOT NULL DEFAULT '',
                    completed_tasks INTEGER NOT NULL DEFAULT 0,
                    total_tasks INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cases (
                    case_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    bp TEXT NOT NULL,
                    credit_segment TEXT NOT NULL,
                    requested_cl_usd REAL NOT NULL DEFAULT 0,
                    statement_currency TEXT NOT NULL,
                    customer TEXT NOT NULL DEFAULT '{}',
                    expected_entity_id TEXT NOT NULL DEFAULT '',
                    business TEXT NOT NULL DEFAULT '',
                    new_year TEXT NOT NULL DEFAULT '{}',
                    inputs TEXT NOT NULL DEFAULT '{}',
                    mock INTEGER NOT NULL DEFAULT 1,
                    package TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    sources TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS human_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_id TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    payload TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    resolved_at TEXT
                );
                CREATE TABLE IF NOT EXISTS case_notes (
                    case_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bad_cases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_id TEXT NOT NULL,
                    node TEXT NOT NULL,
                    failure_type TEXT NOT NULL,
                    exposure TEXT NOT NULL,
                    impact TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    detection_source TEXT NOT NULL,
                    response TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS stage_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    stage_index INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    payload TEXT NOT NULL DEFAULT '{}',
                    completed_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_stage_snapshots_case_stage
                    ON stage_snapshots(case_id, stage, id);
                CREATE TABLE IF NOT EXISTS knowledge_documents (
                    id TEXT PRIMARY KEY,
                    library_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    citation TEXT NOT NULL,
                    source_content TEXT NOT NULL DEFAULT '',
                    source_filename TEXT NOT NULL DEFAULT '',
                    mime_type TEXT NOT NULL DEFAULT 'text/markdown',
                    metadata TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS knowledge_chunks (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    citation TEXT NOT NULL,
                    section TEXT NOT NULL DEFAULT '',
                    page INTEGER,
                    content TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS data_migrations (
                    id TEXT PRIMARY KEY,
                    completed_at TEXT NOT NULL
                );
                """
            )
            columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(knowledge_documents)")
            }
            for name, declaration in (
                ("source_content", "TEXT NOT NULL DEFAULT ''"),
                ("source_filename", "TEXT NOT NULL DEFAULT ''"),
                ("mime_type", "TEXT NOT NULL DEFAULT 'text/markdown'"),
            ):
                if name not in columns:
                    conn.execute(
                        f"ALTER TABLE knowledge_documents ADD COLUMN {name} {declaration}"
                    )
            conn.commit()

    def upsert_case(self, payload: Dict[str, Any], *, overwrite: bool = False) -> None:
        timestamp = now_iso()
        values = (
            payload["case_id"],
            payload.get("title", ""),
            payload.get("bp", ""),
            payload.get("credit_segment", ""),
            float(payload.get("requested_cl_usd") or 0),
            payload.get("statement_currency", ""),
            json.dumps(payload.get("customer") or {}, ensure_ascii=False),
            payload.get("expected_entity_id", ""),
            payload.get("business", ""),
            json.dumps(payload.get("new_year") or {}, ensure_ascii=False),
            json.dumps(payload.get("inputs") or {}, ensure_ascii=False),
            1 if payload.get("mock", True) else 0,
            payload.get("package"),
            timestamp,
            timestamp,
        )
        conflict = (
            "DO UPDATE SET title=excluded.title, bp=excluded.bp, "
            "credit_segment=excluded.credit_segment, "
            "requested_cl_usd=excluded.requested_cl_usd, "
            "statement_currency=excluded.statement_currency, "
            "customer=excluded.customer, expected_entity_id=excluded.expected_entity_id, "
            "business=excluded.business, new_year=excluded.new_year, "
            "inputs=excluded.inputs, mock=excluded.mock, package=excluded.package, "
            "updated_at=excluded.updated_at"
            if overwrite
            else "DO NOTHING"
        )
        with closing(self.connect()) as conn:
            conn.execute(
                f"""
                INSERT INTO cases
                (case_id, title, bp, credit_segment, requested_cl_usd,
                 statement_currency, customer, expected_entity_id, business,
                 new_year, inputs, mock, package, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(case_id) {conflict}
                """,
                values,
            )
            conn.commit()

    @staticmethod
    def _case_row(row: sqlite3.Row) -> Dict[str, Any]:
        item = dict(row)
        for key in ("customer", "new_year", "inputs"):
            item[key] = json.loads(item[key] or "{}")
        item["mock"] = bool(item["mock"])
        item.pop("created_at", None)
        item.pop("updated_at", None)
        return item

    def case(self, case_id: str) -> Optional[Dict[str, Any]]:
        with closing(self.connect()) as conn:
            row = conn.execute(
                "SELECT * FROM cases WHERE case_id = ?", (case_id,)
            ).fetchone()
        return self._case_row(row) if row else None

    def cases(self) -> List[Dict[str, Any]]:
        with closing(self.connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM cases ORDER BY case_id"
            ).fetchall()
        return [self._case_row(row) for row in rows]

    def case_summaries(self) -> List[Dict[str, Any]]:
        """SQLite equivalent of the lightweight PostgreSQL workbench query."""
        with closing(self.connect()) as conn:
            rows = conn.execute(
                """
                SELECT
                  c.case_id, c.title, c.bp, c.requested_cl_usd, c.customer,
                  w.status, w.current_stage, w.stage_index, w.current_task,
                  w.blocker, w.completed_tasks, w.total_tasks,
                  w.updated_at AS workflow_updated_at
                FROM cases c
                JOIN workflow_state w ON w.case_id = c.case_id
                ORDER BY c.case_id
                """
            ).fetchall()
        return [
            {
                "case_id": row["case_id"],
                "title": row["title"],
                "bp": row["bp"],
                "requested_cl_usd": row["requested_cl_usd"],
                "customer": json.loads(row["customer"] or "{}"),
                "workflow": {
                    "case_id": row["case_id"],
                    "status": row["status"],
                    "current_stage": row["current_stage"],
                    "stage_index": row["stage_index"],
                    "current_task": row["current_task"],
                    "blocker": row["blocker"],
                    "completed_tasks": row["completed_tasks"],
                    "total_tasks": row["total_tasks"],
                    "updated_at": row["workflow_updated_at"],
                    "total_stages": len(STAGES) - 1,
                    "stages": STAGES[:-1],
                },
            }
            for row in rows
        ]

    def ensure_case(
        self,
        case_id: str,
        *,
        status: str = "ready",
        stage: str = "resolve_entity",
        task: str = "核对企业主体候选",
        completed_tasks: int = 1,
        total_tasks: int = 6,
    ) -> None:
        with closing(self.connect()) as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO workflow_state
                (case_id, status, current_stage, stage_index, current_task, blocker,
                 completed_tasks, total_tasks, updated_at)
                VALUES (?, ?, ?, ?, ?, '', ?, ?, ?)
                """,
                (
                    case_id,
                    status,
                    stage,
                    STAGES.index(stage) if stage in STAGES else 0,
                    task,
                    completed_tasks,
                    total_tasks,
                    now_iso(),
                ),
            )
            conn.commit()

    def workflow(self, case_id: str) -> Dict[str, Any]:
        self.ensure_case(case_id)
        with closing(self.connect()) as conn:
            row = conn.execute(
                "SELECT * FROM workflow_state WHERE case_id = ?", (case_id,)
            ).fetchone()
        data = dict(row)
        data["total_stages"] = len(STAGES) - 1
        data["stages"] = STAGES[:-1]
        return data

    def update_workflow(self, case_id: str, **changes: Any) -> Dict[str, Any]:
        self.ensure_case(case_id)
        allowed = {
            "status",
            "current_stage",
            "stage_index",
            "current_task",
            "blocker",
            "completed_tasks",
            "total_tasks",
        }
        clean = {k: v for k, v in changes.items() if k in allowed}
        if "current_stage" in clean and "stage_index" not in clean:
            stage = clean["current_stage"]
            clean["stage_index"] = STAGES.index(stage) if stage in STAGES else 0
        clean["updated_at"] = now_iso()
        assignments = ", ".join(f"{key} = ?" for key in clean)
        values = list(clean.values()) + [case_id]
        with closing(self.connect()) as conn:
            current_row = conn.execute(
                "SELECT * FROM workflow_state WHERE case_id = ?", (case_id,)
            ).fetchone()
            current = dict(current_row)
            current["total_stages"] = len(STAGES) - 1
            current["stages"] = STAGES[:-1]
            next_stage = clean.get("current_stage")
            if next_stage and next_stage != current["current_stage"]:
                note_row = conn.execute(
                    "SELECT payload FROM case_notes WHERE case_id = ?", (case_id,)
                ).fetchone()
                notes = json.loads(note_row["payload"] or "{}") if note_row else {}
                conn.execute(
                    """
                    INSERT INTO stage_snapshots
                    (case_id, stage, stage_index, title, payload, completed_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        case_id,
                        current["current_stage"],
                        current["stage_index"],
                        current["current_task"],
                        json.dumps(
                            {"workflow": current, "notes": notes},
                            ensure_ascii=False,
                        ),
                        now_iso(),
                    ),
                )
            conn.execute(
                f"UPDATE workflow_state SET {assignments} WHERE case_id = ?", values
            )
            conn.commit()
        return self.workflow(case_id)

    def add_event(
        self,
        case_id: str,
        event_type: str,
        title: str,
        detail: str,
        sources: Optional[Iterable[str]] = None,
    ) -> None:
        with closing(self.connect()) as conn:
            conn.execute(
                """
                INSERT INTO agent_events
                (case_id, event_type, title, detail, sources, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (case_id, event_type, title, detail, json.dumps(list(sources or []), ensure_ascii=False), now_iso()),
            )
            conn.commit()

    def events(self, case_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        with closing(self.connect()) as conn:
            rows = conn.execute(
                """
                SELECT * FROM agent_events WHERE case_id = ?
                ORDER BY id DESC LIMIT ?
                """,
                (case_id, limit),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["sources"] = json.loads(item["sources"] or "[]")
            result.append(item)
        return result

    def open_task(
        self,
        case_id: str,
        task_type: str,
        title: str,
        description: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> int:
        with closing(self.connect()) as conn:
            existing = conn.execute(
                """
                SELECT id FROM human_tasks
                WHERE case_id = ? AND task_type = ? AND status = 'open'
                """,
                (case_id, task_type),
            ).fetchone()
            if existing:
                return int(existing["id"])
            cur = conn.execute(
                """
                INSERT INTO human_tasks
                (case_id, task_type, title, description, status, payload, created_at)
                VALUES (?, ?, ?, ?, 'open', ?, ?)
                """,
                (
                    case_id,
                    task_type,
                    title,
                    description,
                    json.dumps(payload or {}, ensure_ascii=False),
                    now_iso(),
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def tasks(self, case_id: str, *, open_only: bool = False) -> List[Dict[str, Any]]:
        where = "AND status = 'open'" if open_only else ""
        with closing(self.connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM human_tasks WHERE case_id = ? {where}
                ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, id DESC
                """,
                (case_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item["payload"] or "{}")
            result.append(item)
        return result

    def resolve_task(self, case_id: str, task_id: int) -> Dict[str, Any]:
        with closing(self.connect()) as conn:
            row = conn.execute(
                "SELECT * FROM human_tasks WHERE id = ? AND case_id = ?",
                (task_id, case_id),
            ).fetchone()
            if row is None:
                raise KeyError(task_id)
            conn.execute(
                """
                UPDATE human_tasks SET status = 'resolved', resolved_at = ?
                WHERE id = ?
                """,
                (now_iso(), task_id),
            )
            conn.commit()
        item = dict(row)
        item["payload"] = json.loads(item["payload"] or "{}")
        return item

    def set_notes(self, case_id: str, payload: Dict[str, Any]) -> None:
        with closing(self.connect()) as conn:
            conn.execute(
                """
                INSERT INTO case_notes (case_id, payload, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(case_id) DO UPDATE SET
                  payload = excluded.payload,
                  updated_at = excluded.updated_at
                """,
                (case_id, json.dumps(payload, ensure_ascii=False), now_iso()),
            )
            conn.commit()

    def notes(self, case_id: str) -> Dict[str, Any]:
        with closing(self.connect()) as conn:
            row = conn.execute(
                "SELECT payload FROM case_notes WHERE case_id = ?", (case_id,)
            ).fetchone()
        return json.loads(row["payload"]) if row else {}

    def add_bad_case(
        self,
        case_id: str,
        *,
        node: str,
        failure_type: str,
        exposure: str,
        impact: str,
        priority: str,
        detection_source: str,
        response: str,
    ) -> int:
        with closing(self.connect()) as conn:
            existing = conn.execute(
                """
                SELECT id FROM bad_cases
                WHERE case_id = ? AND node = ? AND failure_type = ? AND status = 'open'
                """,
                (case_id, node, failure_type),
            ).fetchone()
            if existing:
                return int(existing["id"])
            cur = conn.execute(
                """
                INSERT INTO bad_cases
                (case_id, node, failure_type, exposure, impact, priority,
                 detection_source, response, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
                """,
                (
                    case_id,
                    node,
                    failure_type,
                    exposure,
                    impact,
                    priority,
                    detection_source,
                    response,
                    now_iso(),
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def bad_cases(self, case_id: str) -> List[Dict[str, Any]]:
        with closing(self.connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM bad_cases WHERE case_id = ? ORDER BY id DESC",
                (case_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def resolve_bad_cases(self, case_id: str, node: str) -> None:
        """补齐材料后关闭该节点的开放 Bad Case，保留原记录用于审计。"""
        with closing(self.connect()) as conn:
            conn.execute(
                """
                UPDATE bad_cases SET status = 'resolved'
                WHERE case_id = ? AND node = ? AND status = 'open'
                """,
                (case_id, node),
            )
            conn.commit()

    def save_stage_snapshot(
        self,
        case_id: str,
        stage: str,
        stage_index: int,
        title: str,
        payload: Dict[str, Any],
    ) -> None:
        with closing(self.connect()) as conn:
            conn.execute(
                """
                INSERT INTO stage_snapshots
                (case_id, stage, stage_index, title, payload, completed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    case_id,
                    stage,
                    stage_index,
                    title,
                    json.dumps(payload, ensure_ascii=False),
                    now_iso(),
                ),
            )
            conn.commit()

    def stage_snapshots(self, case_id: str) -> List[Dict[str, Any]]:
        with closing(self.connect()) as conn:
            rows = conn.execute(
                """
                SELECT * FROM stage_snapshots WHERE case_id = ?
                ORDER BY stage_index, id
                """,
                (case_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item["payload"] or "{}")
            result.append(item)
        return result

    def sync_knowledge(self, chunks: Iterable[Dict[str, Any]]) -> None:
        timestamp = now_iso()
        with closing(self.connect()) as conn:
            for chunk in chunks:
                conn.execute(
                    """
                    INSERT INTO knowledge_documents
                    (id, library_id, title, citation, metadata, updated_at)
                    VALUES (?, ?, ?, ?, '{}', ?)
                    ON CONFLICT(id) DO UPDATE SET
                      library_id=excluded.library_id,
                      title=excluded.title,
                      citation=excluded.citation,
                      updated_at=excluded.updated_at
                    """,
                    (
                        chunk["document_id"],
                        chunk["library_id"],
                        chunk["title"],
                        chunk["citation"],
                        timestamp,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO knowledge_chunks
                    (id, document_id, citation, section, page, content,
                     metadata, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, '{}', ?)
                    ON CONFLICT(id) DO UPDATE SET
                      document_id=excluded.document_id,
                      citation=excluded.citation,
                      section=excluded.section,
                      page=excluded.page,
                      content=excluded.content,
                      updated_at=excluded.updated_at
                    """,
                    (
                        chunk["id"],
                        chunk["document_id"],
                        chunk["citation"],
                        chunk.get("section") or "",
                        chunk.get("page"),
                        chunk["text"],
                        timestamp,
                    ),
                )
            conn.commit()

    def knowledge_chunks(self) -> List[Dict[str, Any]]:
        with closing(self.connect()) as conn:
            rows = conn.execute(
                """
                SELECT id, document_id, citation, section, page, content AS text
                FROM knowledge_chunks ORDER BY document_id, id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def knowledge_document(self, document_id: str) -> Optional[Dict[str, Any]]:
        with closing(self.connect()) as conn:
            row = conn.execute(
                "SELECT * FROM knowledge_documents WHERE id = ?", (document_id,)
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["metadata"] = json.loads(item.get("metadata") or "{}")
        item["chunk_count"] = sum(
            1
            for chunk in self.knowledge_chunks()
            if chunk["document_id"] == document_id
        )
        return item

    def replace_knowledge_document(
        self,
        document: Dict[str, Any],
        chunks: Iterable[Dict[str, Any]],
    ) -> Dict[str, Any]:
        timestamp = now_iso()
        rows = list(chunks)
        with closing(self.connect()) as conn:
            conn.execute(
                """
                INSERT INTO knowledge_documents
                (id, library_id, title, citation, source_content,
                 source_filename, mime_type, metadata, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  library_id=excluded.library_id,
                  title=excluded.title,
                  citation=excluded.citation,
                  source_content=excluded.source_content,
                  source_filename=excluded.source_filename,
                  mime_type=excluded.mime_type,
                  metadata=excluded.metadata,
                  updated_at=excluded.updated_at
                """,
                (
                    document["id"],
                    document["library_id"],
                    document["title"],
                    document["citation"],
                    document["source_content"],
                    document.get("source_filename", ""),
                    document.get("mime_type", "text/markdown"),
                    json.dumps(document.get("metadata") or {}, ensure_ascii=False),
                    timestamp,
                ),
            )
            conn.execute(
                "DELETE FROM knowledge_chunks WHERE document_id = ?",
                (document["id"],),
            )
            for chunk in rows:
                conn.execute(
                    """
                    INSERT INTO knowledge_chunks
                    (id, document_id, citation, section, page, content,
                     metadata, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk["id"],
                        document["id"],
                        chunk["citation"],
                        chunk.get("section") or "",
                        chunk.get("page"),
                        chunk["text"],
                        json.dumps(chunk.get("metadata") or {}, ensure_ascii=False),
                        timestamp,
                    ),
                )
            conn.commit()
        return self.knowledge_document(document["id"])

    def delete_knowledge_document(self, document_id: str) -> bool:
        with closing(self.connect()) as conn:
            conn.execute(
                "DELETE FROM knowledge_chunks WHERE document_id = ?", (document_id,)
            )
            cursor = conn.execute(
                "DELETE FROM knowledge_documents WHERE id = ?", (document_id,)
            )
            conn.commit()
            return cursor.rowcount > 0

    def has_data_migration(self, migration_id: str) -> bool:
        with closing(self.connect()) as conn:
            row = conn.execute(
                "SELECT 1 FROM data_migrations WHERE id = ?", (migration_id,)
            ).fetchone()
        return row is not None

    def mark_data_migration(self, migration_id: str) -> None:
        with closing(self.connect()) as conn:
            conn.execute(
                """
                INSERT INTO data_migrations (id, completed_at)
                VALUES (?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (migration_id, now_iso()),
            )
            conn.commit()


def create_workflow_store(path: Path, database_url: str = ""):
    if database_url.startswith(("postgresql://", "postgres://")):
        try:
            from .store_postgres import PostgresWorkflowStore

            return PostgresWorkflowStore(database_url)
        except ImportError:
            fallback = WorkflowStore(path)
            fallback.startup_warning = (
                "已配置 PostgreSQL，但 Python 环境缺少 psycopg；"
                "当前临时回退到 SQLite。请重新安装后端 requirements.txt。"
            )
            warnings.warn(fallback.startup_warning, RuntimeWarning, stacklevel=2)
            return fallback
    return WorkflowStore(path)
