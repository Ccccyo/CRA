from __future__ import annotations

from contextlib import closing
import json
import os
from queue import Empty, Full, LifoQueue
import sqlite3
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Iterable, List, Optional

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .store import STAGES, now_iso


class _ConnectionPool:
    """Small dependency-free psycopg pool for the long-running local API process."""

    def __init__(self, database_url: str, max_size: int = 5):
        self.database_url = database_url
        self.max_size = max(1, max_size)
        self._idle: LifoQueue[Any] = LifoQueue(maxsize=self.max_size)
        self._lock = Lock()
        self._total = 0

    def _open(self):
        # Neon may suspend an idle compute or close a pooled socket. Bound every
        # network and SQL wait so a stale connection can never leave the UI on a
        # loading skeleton forever.
        return psycopg.connect(
            self.database_url,
            row_factory=dict_row,
            connect_timeout=int(os.environ.get("CRA_DB_CONNECT_TIMEOUT", "8")),
            options=(
                "-c statement_timeout=12000 "
                "-c lock_timeout=5000 "
                "-c idle_in_transaction_session_timeout=12000"
            ),
            keepalives=1,
            keepalives_idle=5,
            keepalives_interval=5,
            keepalives_count=2,
            tcp_user_timeout=12000,
        )

    def _discard(self, connection: Any) -> None:
        try:
            connection.close()
        finally:
            with self._lock:
                self._total = max(0, self._total - 1)

    def acquire(self):
        while True:
            try:
                connection = self._idle.get_nowait()
                if connection.closed:
                    self._discard(connection)
                    continue
                return connection
            except Empty:
                with self._lock:
                    create = self._total < self.max_size
                    if create:
                        self._total += 1
                if create:
                    try:
                        return self._open()
                    except Exception:
                        with self._lock:
                            self._total = max(0, self._total - 1)
                        raise
                connection = self._idle.get(timeout=5)
                if connection.closed:
                    self._discard(connection)
                    continue
                return connection

    def release(self, connection: Any) -> None:
        if connection.closed:
            self._discard(connection)
            return
        try:
            if connection.info.transaction_status != TransactionStatus.IDLE:
                connection.rollback()
            self._idle.put_nowait(connection)
        except (Exception, Full):
            self._discard(connection)


class _PooledConnection:
    def __init__(self, pool: _ConnectionPool, connection: Any):
        self._pool = pool
        self._connection = connection
        self._released = False

    def __getattr__(self, name: str):
        return getattr(self._connection, name)

    def close(self) -> None:
        if self._released:
            return
        self._released = True
        self._pool.release(self._connection)


class PostgresWorkflowStore:
    """PostgreSQL-backed CRA repository used by the runtime service."""

    backend = "postgresql"
    startup_warning = ""

    def __init__(self, database_url: str):
        self.database_url = database_url
        self._pool = _ConnectionPool(
            database_url,
            max_size=int(os.environ.get("CRA_DB_POOL_SIZE", "5")),
        )
        self._init()

    def connect(self):
        return _PooledConnection(self._pool, self._pool.acquire())

    def _init(self) -> None:
        with closing(self.connect()) as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS cra_case (
                    case_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    bp TEXT NOT NULL,
                    credit_segment TEXT NOT NULL,
                    requested_cl_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
                    statement_currency TEXT NOT NULL,
                    customer JSONB NOT NULL DEFAULT '{}'::jsonb,
                    expected_entity_id TEXT NOT NULL DEFAULT '',
                    business TEXT NOT NULL DEFAULT '',
                    new_year JSONB NOT NULL DEFAULT '{}'::jsonb,
                    inputs JSONB NOT NULL DEFAULT '{}'::jsonb,
                    mock BOOLEAN NOT NULL DEFAULT TRUE,
                    package TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS cra_workflow_state (
                    case_id TEXT PRIMARY KEY REFERENCES cra_case(case_id) ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    current_stage TEXT NOT NULL,
                    stage_index INTEGER NOT NULL,
                    current_task TEXT NOT NULL,
                    blocker TEXT NOT NULL DEFAULT '',
                    completed_tasks INTEGER NOT NULL DEFAULT 0,
                    total_tasks INTEGER NOT NULL DEFAULT 0,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS cra_agent_event (
                    id SERIAL PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cra_case(case_id) ON DELETE CASCADE,
                    event_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    sources JSONB NOT NULL DEFAULT '[]'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_cra_agent_event_case
                    ON cra_agent_event(case_id, id);
                CREATE TABLE IF NOT EXISTS cra_human_task (
                    id SERIAL PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cra_case(case_id) ON DELETE CASCADE,
                    task_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    resolved_at TIMESTAMPTZ
                );
                CREATE INDEX IF NOT EXISTS idx_cra_human_task_case_status
                    ON cra_human_task(case_id, status);
                CREATE TABLE IF NOT EXISTS cra_case_note (
                    case_id TEXT PRIMARY KEY REFERENCES cra_case(case_id) ON DELETE CASCADE,
                    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS cra_bad_case (
                    id SERIAL PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cra_case(case_id) ON DELETE CASCADE,
                    node TEXT NOT NULL,
                    failure_type TEXT NOT NULL,
                    exposure TEXT NOT NULL,
                    impact TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    detection_source TEXT NOT NULL,
                    response TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_cra_bad_case_case_status
                    ON cra_bad_case(case_id, status);
                CREATE TABLE IF NOT EXISTS cra_stage_snapshot (
                    id SERIAL PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cra_case(case_id) ON DELETE CASCADE,
                    stage TEXT NOT NULL,
                    stage_index INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                    completed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_cra_stage_snapshot_case_stage
                    ON cra_stage_snapshot(case_id, stage);
                CREATE TABLE IF NOT EXISTS cra_knowledge_document (
                    id TEXT PRIMARY KEY,
                    library_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    citation TEXT NOT NULL,
                    source_content TEXT NOT NULL DEFAULT '',
                    source_filename TEXT NOT NULL DEFAULT '',
                    mime_type TEXT NOT NULL DEFAULT 'text/markdown',
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                ALTER TABLE cra_knowledge_document
                    ADD COLUMN IF NOT EXISTS source_content TEXT NOT NULL DEFAULT '';
                ALTER TABLE cra_knowledge_document
                    ADD COLUMN IF NOT EXISTS source_filename TEXT NOT NULL DEFAULT '';
                ALTER TABLE cra_knowledge_document
                    ADD COLUMN IF NOT EXISTS mime_type TEXT NOT NULL DEFAULT 'text/markdown';
                CREATE INDEX IF NOT EXISTS idx_cra_knowledge_document_library
                    ON cra_knowledge_document(library_id);
                CREATE TABLE IF NOT EXISTS cra_knowledge_chunk (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL REFERENCES cra_knowledge_document(id) ON DELETE CASCADE,
                    citation TEXT NOT NULL,
                    section TEXT NOT NULL DEFAULT '',
                    page INTEGER,
                    content TEXT NOT NULL,
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_cra_knowledge_chunk_document
                    ON cra_knowledge_chunk(document_id);
                CREATE TABLE IF NOT EXISTS cra_data_migration (
                    id TEXT PRIMARY KEY,
                    completed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
            conn.commit()

    def migrate_legacy_sqlite(self, path: Path) -> None:
        """Import the former local workbench once, preserving demo progress."""
        if not path.is_file():
            return
        with closing(self.connect()) as conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM cra_data_migration WHERE id = %s",
                ("sqlite-workbench-v1",),
            )
            if cursor.fetchone():
                return

        legacy = sqlite3.connect(path)
        legacy.row_factory = sqlite3.Row
        try:
            with closing(self.connect()) as conn, conn.cursor() as cursor:
                for row in legacy.execute("SELECT * FROM workflow_state"):
                    if self.case(row["case_id"]) is None:
                        continue
                    cursor.execute(
                        """
                        INSERT INTO cra_workflow_state
                        (case_id, status, current_stage, stage_index, current_task,
                         blocker, completed_tasks, total_tasks, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT(case_id) DO UPDATE SET
                          status=EXCLUDED.status,
                          current_stage=EXCLUDED.current_stage,
                          stage_index=EXCLUDED.stage_index,
                          current_task=EXCLUDED.current_task,
                          blocker=EXCLUDED.blocker,
                          completed_tasks=EXCLUDED.completed_tasks,
                          total_tasks=EXCLUDED.total_tasks,
                          updated_at=EXCLUDED.updated_at
                        """,
                        tuple(row),
                    )
                for row in legacy.execute("SELECT * FROM agent_events ORDER BY id"):
                    cursor.execute(
                        """
                        INSERT INTO cra_agent_event
                        (case_id, event_type, title, detail, sources, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            row["case_id"],
                            row["event_type"],
                            row["title"],
                            row["detail"],
                            Jsonb(json.loads(row["sources"] or "[]")),
                            row["created_at"],
                        ),
                    )
                for row in legacy.execute("SELECT * FROM human_tasks ORDER BY id"):
                    cursor.execute(
                        """
                        INSERT INTO cra_human_task
                        (case_id, task_type, title, description, status, payload,
                         created_at, resolved_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            row["case_id"],
                            row["task_type"],
                            row["title"],
                            row["description"],
                            row["status"],
                            Jsonb(json.loads(row["payload"] or "{}")),
                            row["created_at"],
                            row["resolved_at"],
                        ),
                    )
                for row in legacy.execute("SELECT * FROM case_notes"):
                    cursor.execute(
                        """
                        INSERT INTO cra_case_note (case_id, payload, updated_at)
                        VALUES (%s, %s, %s)
                        ON CONFLICT(case_id) DO UPDATE SET
                          payload=EXCLUDED.payload, updated_at=EXCLUDED.updated_at
                        """,
                        (
                            row["case_id"],
                            Jsonb(json.loads(row["payload"] or "{}")),
                            row["updated_at"],
                        ),
                    )
                for row in legacy.execute("SELECT * FROM bad_cases ORDER BY id"):
                    cursor.execute(
                        """
                        INSERT INTO cra_bad_case
                        (case_id, node, failure_type, exposure, impact, priority,
                         detection_source, response, status, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            row["case_id"],
                            row["node"],
                            row["failure_type"],
                            row["exposure"],
                            row["impact"],
                            row["priority"],
                            row["detection_source"],
                            row["response"],
                            row["status"],
                            row["created_at"],
                        ),
                    )
                if any(
                    row[0] == "stage_snapshots"
                    for row in legacy.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                ):
                    for row in legacy.execute(
                        "SELECT * FROM stage_snapshots ORDER BY id"
                    ):
                        cursor.execute(
                            """
                            INSERT INTO cra_stage_snapshot
                            (case_id, stage, stage_index, title, payload, completed_at)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            """,
                            (
                                row["case_id"],
                                row["stage"],
                                row["stage_index"],
                                row["title"],
                                Jsonb(json.loads(row["payload"] or "{}")),
                                row["completed_at"],
                            ),
                        )
                cursor.execute(
                    "INSERT INTO cra_data_migration (id) VALUES (%s)",
                    ("sqlite-workbench-v1",),
                )
                conn.commit()
        finally:
            legacy.close()

    def upsert_case(self, payload: Dict[str, Any], *, overwrite: bool = False) -> None:
        conflict = (
            "DO UPDATE SET title=EXCLUDED.title, bp=EXCLUDED.bp, "
            "credit_segment=EXCLUDED.credit_segment, "
            "requested_cl_usd=EXCLUDED.requested_cl_usd, "
            "statement_currency=EXCLUDED.statement_currency, "
            "customer=EXCLUDED.customer, expected_entity_id=EXCLUDED.expected_entity_id, "
            "business=EXCLUDED.business, new_year=EXCLUDED.new_year, "
            "inputs=EXCLUDED.inputs, mock=EXCLUDED.mock, package=EXCLUDED.package, "
            "updated_at=NOW()"
            if overwrite
            else "DO NOTHING"
        )
        with closing(self.connect()) as conn, conn.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO cra_case
                (case_id, title, bp, credit_segment, requested_cl_usd,
                 statement_currency, customer, expected_entity_id, business,
                 new_year, inputs, mock, package)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(case_id) {conflict}
                """,
                (
                    payload["case_id"],
                    payload.get("title", ""),
                    payload.get("bp", ""),
                    payload.get("credit_segment", ""),
                    float(payload.get("requested_cl_usd") or 0),
                    payload.get("statement_currency", ""),
                    Jsonb(payload.get("customer") or {}),
                    payload.get("expected_entity_id", ""),
                    payload.get("business", ""),
                    Jsonb(payload.get("new_year") or {}),
                    Jsonb(payload.get("inputs") or {}),
                    bool(payload.get("mock", True)),
                    payload.get("package"),
                ),
            )
            conn.commit()

    @staticmethod
    def _case_row(row: Dict[str, Any]) -> Dict[str, Any]:
        item = dict(row)
        item.pop("created_at", None)
        item.pop("updated_at", None)
        return item

    def case(self, case_id: str) -> Optional[Dict[str, Any]]:
        with closing(self.connect()) as conn, conn.cursor() as cursor:
            cursor.execute("SELECT * FROM cra_case WHERE case_id = %s", (case_id,))
            row = cursor.fetchone()
        return self._case_row(row) if row else None

    def cases(self) -> List[Dict[str, Any]]:
        with closing(self.connect()) as conn, conn.cursor() as cursor:
            cursor.execute("SELECT * FROM cra_case ORDER BY case_id")
            rows = cursor.fetchall()
        return [self._case_row(row) for row in rows]

    def case_summaries(self) -> List[Dict[str, Any]]:
        """Return the fields needed by the workbench in one database round trip."""
        with closing(self.connect()) as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                  c.case_id, c.title, c.bp, c.requested_cl_usd, c.customer,
                  w.status, w.current_stage, w.stage_index, w.current_task,
                  w.blocker, w.completed_tasks, w.total_tasks,
                  w.updated_at AS workflow_updated_at
                FROM cra_case c
                JOIN cra_workflow_state w ON w.case_id = c.case_id
                ORDER BY c.case_id
                """
            )
            rows = cursor.fetchall()
        return [
            {
                "case_id": row["case_id"],
                "title": row["title"],
                "bp": row["bp"],
                "requested_cl_usd": row["requested_cl_usd"],
                "customer": row["customer"],
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

    def case_context(self, case_id: str) -> Optional[Dict[str, Any]]:
        """Load workflow, tasks, evidence and audit history with one query."""
        with closing(self.connect()) as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                  to_jsonb(w) AS workflow,
                  COALESCE((
                    SELECT jsonb_agg(to_jsonb(t) ORDER BY
                      CASE t.status WHEN 'open' THEN 0 ELSE 1 END, t.id DESC)
                    FROM cra_human_task t WHERE t.case_id = w.case_id
                  ), '[]'::jsonb) AS tasks,
                  COALESCE((
                    SELECT n.payload FROM cra_case_note n WHERE n.case_id = w.case_id
                  ), '{}'::jsonb) AS notes,
                  COALESCE((
                    SELECT jsonb_agg(to_jsonb(recent) ORDER BY recent.id DESC)
                    FROM (
                      SELECT * FROM cra_agent_event e
                      WHERE e.case_id = w.case_id
                      ORDER BY e.id DESC LIMIT 20
                    ) recent
                  ), '[]'::jsonb) AS events,
                  COALESCE((
                    SELECT jsonb_agg(to_jsonb(b) ORDER BY
                      CASE b.status WHEN 'open' THEN 0 ELSE 1 END, b.id DESC)
                    FROM cra_bad_case b WHERE b.case_id = w.case_id
                  ), '[]'::jsonb) AS bad_cases,
                  COALESCE((
                    SELECT jsonb_agg(to_jsonb(s) ORDER BY s.stage_index, s.id)
                    FROM cra_stage_snapshot s WHERE s.case_id = w.case_id
                  ), '[]'::jsonb) AS stage_snapshots
                FROM cra_workflow_state w
                WHERE w.case_id = %s
                """,
                (case_id,),
            )
            row = cursor.fetchone()
        if not row:
            return None
        result = dict(row)
        result["workflow"] = dict(result["workflow"])
        result["workflow"]["total_stages"] = len(STAGES) - 1
        result["workflow"]["stages"] = STAGES[:-1]
        return result

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
        with closing(self.connect()) as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO cra_workflow_state
                (case_id, status, current_stage, stage_index, current_task,
                 blocker, completed_tasks, total_tasks)
                VALUES (%s, %s, %s, %s, %s, '', %s, %s)
                ON CONFLICT(case_id) DO NOTHING
                """,
                (
                    case_id,
                    status,
                    stage,
                    STAGES.index(stage) if stage in STAGES else 0,
                    task,
                    completed_tasks,
                    total_tasks,
                ),
            )
            conn.commit()

    def workflow(self, case_id: str) -> Dict[str, Any]:
        self.ensure_case(case_id)
        with closing(self.connect()) as conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM cra_workflow_state WHERE case_id = %s", (case_id,)
            )
            row = cursor.fetchone()
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
        clean = {key: value for key, value in changes.items() if key in allowed}
        if "current_stage" in clean and "stage_index" not in clean:
            stage = clean["current_stage"]
            clean["stage_index"] = STAGES.index(stage) if stage in STAGES else 0
        clean["updated_at"] = now_iso()
        assignments = ", ".join(f"{key} = %s" for key in clean)
        with closing(self.connect()) as conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM cra_workflow_state WHERE case_id = %s FOR UPDATE",
                (case_id,),
            )
            current = dict(cursor.fetchone())
            current["total_stages"] = len(STAGES) - 1
            current["stages"] = STAGES[:-1]
            next_stage = clean.get("current_stage")
            if next_stage and next_stage != current["current_stage"]:
                cursor.execute(
                    "SELECT payload FROM cra_case_note WHERE case_id = %s",
                    (case_id,),
                )
                note_row = cursor.fetchone()
                notes = note_row["payload"] if note_row else {}
                self._insert_stage_snapshot(
                    cursor,
                    case_id,
                    current["current_stage"],
                    current["stage_index"],
                    current["current_task"],
                    {"workflow": current, "notes": notes},
                )
            cursor.execute(
                f"UPDATE cra_workflow_state SET {assignments} WHERE case_id = %s",
                [*clean.values(), case_id],
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
        with closing(self.connect()) as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO cra_agent_event
                (case_id, event_type, title, detail, sources)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (case_id, event_type, title, detail, Jsonb(list(sources or []))),
            )
            conn.commit()

    def events(self, case_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        with closing(self.connect()) as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM cra_agent_event WHERE case_id = %s
                ORDER BY id DESC LIMIT %s
                """,
                (case_id, limit),
            )
            return list(cursor.fetchall())

    def open_task(
        self,
        case_id: str,
        task_type: str,
        title: str,
        description: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> int:
        with closing(self.connect()) as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id FROM cra_human_task
                WHERE case_id = %s AND task_type = %s AND status = 'open'
                """,
                (case_id, task_type),
            )
            existing = cursor.fetchone()
            if existing:
                return int(existing["id"])
            cursor.execute(
                """
                INSERT INTO cra_human_task
                (case_id, task_type, title, description, status, payload)
                VALUES (%s, %s, %s, %s, 'open', %s) RETURNING id
                """,
                (case_id, task_type, title, description, Jsonb(payload or {})),
            )
            task_id = int(cursor.fetchone()["id"])
            conn.commit()
            return task_id

    def tasks(self, case_id: str, *, open_only: bool = False) -> List[Dict[str, Any]]:
        where = "AND status = 'open'" if open_only else ""
        with closing(self.connect()) as conn, conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT * FROM cra_human_task WHERE case_id = %s {where}
                ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, id DESC
                """,
                (case_id,),
            )
            return list(cursor.fetchall())

    def resolve_task(self, case_id: str, task_id: int) -> Dict[str, Any]:
        with closing(self.connect()) as conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM cra_human_task WHERE id = %s AND case_id = %s",
                (task_id, case_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise KeyError(task_id)
            cursor.execute(
                """
                UPDATE cra_human_task SET status = 'resolved', resolved_at = NOW()
                WHERE id = %s
                """,
                (task_id,),
            )
            conn.commit()
        return dict(row)

    def set_notes(self, case_id: str, payload: Dict[str, Any]) -> None:
        with closing(self.connect()) as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO cra_case_note (case_id, payload)
                VALUES (%s, %s)
                ON CONFLICT(case_id) DO UPDATE SET
                  payload=EXCLUDED.payload, updated_at=NOW()
                """,
                (case_id, Jsonb(payload)),
            )
            conn.commit()

    def notes(self, case_id: str) -> Dict[str, Any]:
        with closing(self.connect()) as conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT payload FROM cra_case_note WHERE case_id = %s", (case_id,)
            )
            row = cursor.fetchone()
        return dict(row["payload"]) if row else {}

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
        with closing(self.connect()) as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id FROM cra_bad_case
                WHERE case_id = %s AND node = %s AND failure_type = %s
                  AND status = 'open'
                """,
                (case_id, node, failure_type),
            )
            existing = cursor.fetchone()
            if existing:
                return int(existing["id"])
            cursor.execute(
                """
                INSERT INTO cra_bad_case
                (case_id, node, failure_type, exposure, impact, priority,
                 detection_source, response, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'open') RETURNING id
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
                ),
            )
            bad_case_id = int(cursor.fetchone()["id"])
            conn.commit()
            return bad_case_id

    def bad_cases(self, case_id: str) -> List[Dict[str, Any]]:
        with closing(self.connect()) as conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM cra_bad_case WHERE case_id = %s ORDER BY id DESC",
                (case_id,),
            )
            return list(cursor.fetchall())

    def resolve_bad_cases(self, case_id: str, node: str) -> None:
        with closing(self.connect()) as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE cra_bad_case SET status = 'resolved'
                WHERE case_id = %s AND node = %s AND status = 'open'
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
        with closing(self.connect()) as conn, conn.cursor() as cursor:
            self._insert_stage_snapshot(
                cursor, case_id, stage, stage_index, title, payload
            )
            conn.commit()

    @staticmethod
    def _insert_stage_snapshot(
        cursor,
        case_id: str,
        stage: str,
        stage_index: int,
        title: str,
        payload: Dict[str, Any],
    ) -> None:
        cursor.execute(
            """
            INSERT INTO cra_stage_snapshot
            (case_id, stage, stage_index, title, payload)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                case_id,
                stage,
                stage_index,
                title,
                Jsonb(json.loads(json.dumps(payload, default=str))),
            ),
        )

    def stage_snapshots(self, case_id: str) -> List[Dict[str, Any]]:
        with closing(self.connect()) as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM cra_stage_snapshot WHERE case_id = %s
                ORDER BY stage_index, id
                """,
                (case_id,),
            )
            return list(cursor.fetchall())

    def sync_knowledge(self, chunks: Iterable[Dict[str, Any]]) -> None:
        with closing(self.connect()) as conn, conn.cursor() as cursor:
            for chunk in chunks:
                cursor.execute(
                    """
                    INSERT INTO cra_knowledge_document
                    (id, library_id, title, citation)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT(id) DO UPDATE SET
                      library_id=EXCLUDED.library_id,
                      title=EXCLUDED.title,
                      citation=EXCLUDED.citation,
                      updated_at=NOW()
                    """,
                    (
                        chunk["document_id"],
                        chunk["library_id"],
                        chunk["title"],
                        chunk["citation"],
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO cra_knowledge_chunk
                    (id, document_id, citation, section, page, content)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT(id) DO UPDATE SET
                      document_id=EXCLUDED.document_id,
                      citation=EXCLUDED.citation,
                      section=EXCLUDED.section,
                      page=EXCLUDED.page,
                      content=EXCLUDED.content,
                      updated_at=NOW()
                    """,
                    (
                        chunk["id"],
                        chunk["document_id"],
                        chunk["citation"],
                        chunk.get("section") or "",
                        chunk.get("page"),
                        chunk["text"],
                    ),
                )
            conn.commit()

    def knowledge_chunks(self) -> List[Dict[str, Any]]:
        with closing(self.connect()) as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, document_id, citation, section, page, content AS text
                FROM cra_knowledge_chunk ORDER BY document_id, id
                """
            )
            return list(cursor.fetchall())

    def knowledge_document(self, document_id: str) -> Optional[Dict[str, Any]]:
        with closing(self.connect()) as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT document.*, COUNT(chunk.id)::integer AS chunk_count
                FROM cra_knowledge_document AS document
                LEFT JOIN cra_knowledge_chunk AS chunk
                  ON chunk.document_id = document.id
                WHERE document.id = %s
                GROUP BY document.id
                """,
                (document_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def replace_knowledge_document(
        self,
        document: Dict[str, Any],
        chunks: Iterable[Dict[str, Any]],
    ) -> Dict[str, Any]:
        rows = list(chunks)
        with closing(self.connect()) as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO cra_knowledge_document
                (id, library_id, title, citation, source_content,
                 source_filename, mime_type, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(id) DO UPDATE SET
                  library_id=EXCLUDED.library_id,
                  title=EXCLUDED.title,
                  citation=EXCLUDED.citation,
                  source_content=EXCLUDED.source_content,
                  source_filename=EXCLUDED.source_filename,
                  mime_type=EXCLUDED.mime_type,
                  metadata=EXCLUDED.metadata,
                  updated_at=NOW()
                """,
                (
                    document["id"],
                    document["library_id"],
                    document["title"],
                    document["citation"],
                    document["source_content"],
                    document.get("source_filename", ""),
                    document.get("mime_type", "text/markdown"),
                    Jsonb(document.get("metadata") or {}),
                ),
            )
            cursor.execute(
                "DELETE FROM cra_knowledge_chunk WHERE document_id = %s",
                (document["id"],),
            )
            for chunk in rows:
                cursor.execute(
                    """
                    INSERT INTO cra_knowledge_chunk
                    (id, document_id, citation, section, page, content, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        chunk["id"],
                        document["id"],
                        chunk["citation"],
                        chunk.get("section") or "",
                        chunk.get("page"),
                        chunk["text"],
                        Jsonb(chunk.get("metadata") or {}),
                    ),
                )
            conn.commit()
        return self.knowledge_document(document["id"])

    def delete_knowledge_document(self, document_id: str) -> bool:
        with closing(self.connect()) as conn, conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM cra_knowledge_document WHERE id = %s RETURNING id",
                (document_id,),
            )
            deleted = cursor.fetchone() is not None
            conn.commit()
            return deleted

    def has_data_migration(self, migration_id: str) -> bool:
        with closing(self.connect()) as conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM cra_data_migration WHERE id = %s", (migration_id,)
            )
            return cursor.fetchone() is not None

    def mark_data_migration(self, migration_id: str) -> None:
        with closing(self.connect()) as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO cra_data_migration (id)
                VALUES (%s)
                ON CONFLICT(id) DO NOTHING
                """,
                (migration_id,),
            )
            conn.commit()
