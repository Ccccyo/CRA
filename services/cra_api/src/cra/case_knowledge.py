"""Materialize the current case state as a private, case-scoped RAG document.

The case database remains authoritative. This projection is derived evidence,
not an approval policy, and must never be retrieved without an authorized case.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any, Dict

from cra.config import get_settings
from cra.ingest.chunker import chunk_markdown


def case_evidence_document_id(case_id: str) -> str:
    digest = hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:24]
    return f"case-evidence-{digest}"


def case_history_document_id(case_id: str) -> str:
    digest = hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:24]
    return f"case-history-{digest}"


def _payload(case: Dict[str, Any], workflow: Dict[str, Any], notes: Dict[str, Any]) -> str:
    sections = [
        ("案件与客户", {
            key: case.get(key) for key in (
                "case_id", "title", "bp", "customer", "business",
                "credit_segment", "requested_cl_usd", "statement_currency", "mock",
            )
        }),
        ("当期材料索引", {"new_year": case.get("new_year"), "inputs": case.get("inputs")}),
        ("流程状态", workflow),
        ("已确认企业主体", notes.get("selected_entity") or {}),
        ("财务证据", {
            "source": notes.get("financial_source") or {},
            "values": notes.get("financial_values") or [],
        }),
        ("风险意见", notes.get("risk_draft") or {}),
        ("人工处理记录", notes.get("human_resolutions") or {}),
    ]
    body = "\n\n".join(
        f"# {heading}\n\n{json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str)}"
        for heading, value in sections if value
    )
    return (
        "本案数据库状态的派生检索快照；原始财报、Excel 和人工签署记录仍须核对。"
        "本案历史与草稿不是现行授信政策，也不构成自动审批。\n\n" + body
    )


def sync_case_evidence(store: Any, case_id: str) -> bool:
    """Idempotently upsert one case projection; return whether it changed."""
    case = store.case(case_id)
    if case is None:
        raise KeyError(case_id)
    workflow = store.workflow(case_id)
    notes = store.notes(case_id)
    content = _payload(case, workflow, notes)
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    document_id = case_evidence_document_id(case_id)
    existing = store.knowledge_document(document_id)
    if existing and (existing.get("metadata") or {}).get("content_hash") == digest:
        sync_case_history(store, case, workflow, notes)
        _close_sync_incident(store, case_id)
        return False
    source = f"本案/{case_id}"
    chunks = chunk_markdown(content, source)
    metadata = {
        "managed_by": "case_knowledge.py", "parser": "case-state-v1",
        "source_kind": "case_evidence", "access_scope": "case_only",
        "case_id": case_id, "content_hash": digest,
        "is_published": True, "status": "case_current",
        "is_mock": bool(case.get("mock", False)),
        "authority": "derived_case_state_not_policy",
    }
    store.replace_knowledge_document(
        {
            "id": document_id, "library_id": "case_evidence",
            "title": f"{case_id} · {case.get('title') or case_id}",
            "citation": source, "source_content": content,
            "source_filename": "", "mime_type": "text/markdown",
            "metadata": metadata,
        },
        [
            {
                "id": f"{document_id}#{index}", "document_id": document_id,
                "citation": chunk.citation, "section": chunk.section,
                "page": chunk.page, "text": chunk.text,
                "metadata": {**metadata, "chunk_index": index},
            }
            for index, chunk in enumerate(chunks)
        ],
    )
    sync_case_history(store, case, workflow, notes)
    _close_sync_incident(store, case_id)
    return True


def sync_case_history(
    store: Any, case: Dict[str, Any], workflow: Dict[str, Any], notes: Dict[str, Any]
) -> bool:
    """Publish only completed mock-case snapshots in the active demo release.

    An in-progress case remains in the case DB and private case_evidence index.
    Completion is a workflow state, not an approved credit decision.
    """
    get_settings()  # Load the project's environment without requiring a DB connection.
    release_id = os.environ.get("CRA_DEMO_KB_RELEASE_ID", "").strip()
    case_id = case["case_id"]
    document_id = case_history_document_id(case_id)
    existing = store.knowledge_document(document_id)
    eligible = bool(
        release_id and case.get("mock") is True
        and workflow.get("status") == "completed"
        and case.get("bp")
    )
    if not eligible:
        if not existing or (existing.get("metadata") or {}).get("is_published") is not True:
            return False
        previous = existing.get("metadata") or {}
        if previous.get("managed_by") != "case_knowledge.py":
            raise ValueError("Refusing to withdraw unmanaged case history")
        withdrawn = {**previous, "is_published": False, "status": "withdrawn"}
        store.replace_knowledge_document(
            {**existing, "source_content": "", "metadata": withdrawn}, []
        )
        return True

    content = (
        "【模拟案件·已完成评审流程】此资料由当前案件数据库派生；流程完成不等于"
        "授信获批。风险意见、历史额度和人工记录均不能替代本次有效政策或签核。"
        "仅供同一 BP 的模拟案件参考；原始附件和签署记录仍须回到案件核对。\n\n"
        + _payload(case, workflow, notes)
    )
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    metadata = {
        "managed_by": "case_knowledge.py", "parser": "case-history-snapshot-v1",
        "source_kind": "completed_case_archive", "access_scope": "same_bp_demo",
        "case_id": case_id, "bp": str(case["bp"]), "content_hash": digest,
        "is_published": True, "status": "demo_published",
        "is_mock": True, "demo_only": True, "demo_release_id": release_id,
        "workflow_status": "completed",
        "authority": "derived_completed_case_not_approval",
    }
    if existing and (existing.get("metadata") or {}) == metadata:
        return False
    if existing and (existing.get("metadata") or {}).get("managed_by") != "case_knowledge.py":
        raise ValueError("Refusing to overwrite unmanaged case history")
    source = f"历史申报/【模拟·已完成流程】{case_id} · {case.get('title') or case_id}"
    chunks = chunk_markdown(content, source)
    store.replace_knowledge_document(
        {
            "id": document_id, "library_id": "case_history",
            "title": f"【模拟·已完成流程】{case_id} · {case.get('title') or case_id}",
            "citation": source, "source_content": content,
            "source_filename": "", "mime_type": "text/markdown",
            "metadata": metadata,
        },
        [
            {
                "id": f"{document_id}#{index}", "document_id": document_id,
                "citation": chunk.citation, "section": chunk.section,
                "page": chunk.page, "text": chunk.text,
                "metadata": {**metadata, "chunk_index": index},
            }
            for index, chunk in enumerate(chunks)
        ],
    )
    return True


def _close_sync_incident(store: Any, case_id: str) -> None:
    try:
        store.resolve_bad_cases(case_id, "case_knowledge_sync")
    except Exception:
        logging.getLogger(__name__).warning(
            "Case knowledge is current but incident resolution failed: %s", case_id
        )


def sync_all_case_evidence(store: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {"cases": 0, "changed": 0, "failed": []}
    for case in store.cases():
        result["cases"] += 1
        try:
            result["changed"] += int(sync_case_evidence(store, case["case_id"]))
        except Exception:
            result["failed"].append(case["case_id"])
            logging.getLogger(__name__).exception(
                "Case knowledge reconciliation failed: %s", case["case_id"]
            )
    return result


def safe_sync_after_write(store: Any, case_id: str) -> bool:
    """Do not misreport a committed business write as failed if indexing fails."""
    try:
        changed = sync_case_evidence(store, case_id)
    except Exception:
        logging.getLogger(__name__).exception(
            "Case knowledge sync failed after a committed write: %s", case_id
        )
        try:
            store.add_bad_case(
                case_id, node="case_knowledge_sync",
                failure_type="案件检索快照更新失败", exposure="L1",
                impact="medium", priority="P1",
                detection_source="案件写入后索引校验",
                response="保留已提交业务状态；下次案件问答或启动对账重建索引",
            )
        except Exception:
            logging.getLogger(__name__).exception(
                "Case knowledge sync failure could not be recorded: %s", case_id
            )
        return False
    return changed
