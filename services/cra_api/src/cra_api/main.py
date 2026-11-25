from __future__ import annotations

import asyncio
import csv
import hashlib
import hmac
import io
import json
import logging
import os
import re
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path
from threading import Thread
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import quote, unquote, urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from openpyxl import load_workbook

from cra.casefile import Case, load_cases, load_fx
from cra.case_knowledge import (
    case_evidence_document_id,
    sync_all_case_evidence,
    sync_case_evidence,
)
from cra.agent.task_router import plan_case_question, review_date_for_question, task_frame
from cra.config import ROOT, get_settings
from cra.countries import COUNTRY_ZH
from cra.demo_financials import DEMO_ASSUMPTIONS, build_demo_financials
from cra.draft.workbench import generate_risk_opinion
from cra.external.gleif_mcp import lookup_lei_via_mcp
from cra.excel_mock import (
    FIELD_ROW,
    fill_ai_results,
    read_workbook,
    save_macro_results,
)
from cra.extract.extractor import check_level, extract_from_pdf
from cra.extract.schema import FIELD_LABELS, ExtractedValue
from cra.handoff import case_fx, case_sap, import_package, read_package, sample_path
from cra.ingest.chunker import Chunk, chunk_markdown, chunk_pages
from cra.ingest.knowledge_parser import (
    SUPPORTED_EXTENSIONS as KNOWLEDGE_EXTENSIONS,
    parse_knowledge_source,
)
from cra.lookup.matcher import LookupResult
from cra.mock_sap import run_macro
from cra.qa.case_chat import answer_case_question
from cra.resources import entity_matcher, knowledge_chunks
from cra.retrieve.runtime import (
    ScopedRetriever,
    is_retrievable_knowledge,
    shared_retriever_cache,
)
from cra.trace import log_event
from cra.validate import Flag

from .access import ROLE_ACCESS, knowledge_catalog, request_actor, source_type
from .store import create_workflow_store, now_iso


app = FastAPI(title="CRA Agent Service", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def require_service_secret(request: Request, call_next):
    """Keep the Python API private; browser users authenticate at the Next.js proxy."""
    shared_secret = get_settings().cra_api_shared_secret
    protected = request.method != "OPTIONS" and request.url.path != "/health"
    if protected and not shared_secret:
        return JSONResponse(
            status_code=503,
            content={"detail": "CRA_API_SHARED_SECRET 尚未配置"},
        )
    if protected and not hmac.compare_digest(
        request.headers.get("x-cra-service-secret", ""), shared_secret
    ):
        return JSONResponse(status_code=401, content={"detail": "无效的 CRA 服务凭证"})
    return await call_next(request)

# Vercel Functions expose a read-only deployment bundle and a writable /tmp.
# Keep local development on the repository runtime directory while making
# serverless instances safe to cold-start. Durable records remain in
# PostgreSQL and original uploads remain in the managed object store.
RUNTIME = get_settings().runtime_dir
UPLOADS = RUNTIME / "uploads"
FINANCIAL_REPORTS = UPLOADS / "financial-reports"
KNOWLEDGE_SOURCES = RUNTIME / "knowledge-sources"
MANAGED_CACHE = RUNTIME / "managed-cache"
EXPORTS = RUNTIME / "exports"
UPLOADS.mkdir(parents=True, exist_ok=True)
FINANCIAL_REPORTS.mkdir(parents=True, exist_ok=True)
KNOWLEDGE_SOURCES.mkdir(parents=True, exist_ok=True)
MANAGED_CACHE.mkdir(parents=True, exist_ok=True)
EXPORTS.mkdir(parents=True, exist_ok=True)
store = create_workflow_store(
    RUNTIME / "workbench.db", get_settings().database_url
)


class AgentAction(BaseModel):
    action: str = "continue"


class ResolveTask(BaseModel):
    resolution: Dict[str, Any] = Field(default_factory=dict)


class FinalReviewPayload(BaseModel):
    final_limit_usd: float = Field(ge=0)
    method: str = Field(min_length=1, max_length=120)
    approval_level: str = Field(min_length=1, max_length=160)
    deviation_reason: str = Field(default="", max_length=1000)
    artifact_checksum: str = Field(min_length=64, max_length=64)


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    history: List[ChatTurn] = Field(default_factory=list, max_length=50)


class RiskOpinionUpdate(BaseModel):
    company_description: str = Field(min_length=1, max_length=2000)
    risk_comments: str = Field(min_length=1, max_length=8000)
    stance: str = Field(default="", max_length=40)
    qualitative: Dict[str, Dict[str, str]] = Field(default_factory=dict)


class CaseTitleUpdate(BaseModel):
    title: str = Field(min_length=2, max_length=160)


class KnowledgeDocumentUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    library_id: Literal["policy", "public_reference", "case_history"]
    content: str = Field(min_length=1, max_length=2_000_000)


MAX_FINANCIAL_REPORT_BYTES = 20 * 1024 * 1024


def require_request_actor(user_id: str, name: str, role: str) -> Dict[str, str]:
    principal = request_actor(user_id, name, role)
    if principal is None:
        raise HTTPException(401, "无法识别当前用户")
    return principal


def require_knowledge_admin(user_id: str, name: str, role: str) -> Dict[str, str]:
    principal = require_request_actor(user_id, name, role)
    if principal["role"] not in {"admin", "super_admin"}:
        raise HTTPException(403, "只有管理员可以修改 RAG 知识库")
    return principal


def require_business_actor(user_id: str, name: str, role: str) -> Dict[str, str]:
    principal = require_request_actor(user_id, name, role)
    if principal["role"] in {"admin", "super_admin"}:
        raise HTTPException(
            403,
            "系统管理员可查看全部案件并负责知识库维护，但不能创建、编辑、运行或签署业务案件",
        )
    return principal


def _indexed_knowledge_chunks() -> List[Chunk]:
    chunks = []
    for row in store.knowledge_chunks():
        metadata = dict(row.get("metadata") or {})
        if not is_retrievable_knowledge(metadata):
            continue
        metadata["library_id"] = row.get("library_id") or source_type(
            row["citation"]
        )
        chunks.append(
            Chunk(
                id=row["id"],
                text=row["text"],
                source=row["citation"].split("（", 1)[0],
                section=row.get("section") or "",
                page=row.get("page"),
                meta=metadata,
            )
        )
    return chunks


def retriever_for(
    principal: Dict[str, str], *, case_id: str = "", case_only: bool = False,
    allowed_libraries: Optional[set[str] | frozenset[str]] = None,
    case: Optional[Case] = None, review_date: str = "",
) -> ScopedRetriever:
    """Return a cheap authorized view over one process-wide shared index."""
    settings = get_settings()
    use_vector = bool(getattr(settings, "rag_use_vector", True))
    use_rerank = bool(getattr(settings, "rag_use_rerank", True))
    revision = (
        id(store),
        store.knowledge_revision(),
        use_vector,
        use_rerank,
        getattr(settings, "embed_model", "BAAI/bge-m3"),
        getattr(settings, "rerank_model", "BAAI/bge-reranker-v2-m3"),
    )
    shared = shared_retriever_cache.get(
        revision,
        _indexed_knowledge_chunks,
        use_vector=use_vector,
        use_rerank=use_rerank,
    )
    grants = set(ROLE_ACCESS.get(principal["role"], set()))
    if allowed_libraries is not None:
        grants &= set(allowed_libraries)
    return ScopedRetriever(
        shared,
        grants,
        case_id=case_id,
        case_only=case_only,
        review_date=review_date or (
            datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat() if case else ""
        ),
        credit_segment=case.credit_segment if case else "",
        region=str(case.customer.get("country") or "") if case else "",
        bp=case.bp if case else "",
        demo_release_id=os.environ.get("CRA_DEMO_KB_RELEASE_ID", "").strip(),
        demo_case=bool(case and case.mock),
    )


LIBRARY_PREFIX = {
    "policy": "",
    "public_reference": "公开资料/",
    "case_history": "历史申报/",
}
MAX_KNOWLEDGE_UPLOAD_BYTES = 10 * 1024 * 1024


def knowledge_source(library_id: str, title: str) -> str:
    return f"{LIBRARY_PREFIX[library_id]}{title.strip()}"


def knowledge_rows(
    document_id: str,
    library_id: str,
    title: str,
    content: str,
    *,
    pages=None,
    metadata=None,
) -> List[Dict[str, Any]]:
    source = knowledge_source(library_id, title)
    chunks = chunk_pages(pages, source) if pages is not None else chunk_markdown(content, source)
    if not chunks:
        raise HTTPException(422, "文档中没有可用于检索的文本")
    return [
        {
            "id": f"{document_id}#{index}",
            "document_id": document_id,
            "citation": chunk.citation,
            "section": chunk.section,
            "page": chunk.page,
            "text": chunk.text,
            "metadata": {**(metadata or {}), "chunk_index": index,
                         "source_page": chunk.page},
        }
        for index, chunk in enumerate(chunks)
    ]


def ensure_case_evidence_document(
    case: Case, workflow: Dict[str, Any], notes: Dict[str, Any]
) -> str:
    """Compatibility wrapper; the database state, not caller data, is indexed."""
    sync_case_evidence(store, case.case_id)
    return case_evidence_document_id(case.case_id)


def knowledge_admin_payload(document: Dict[str, Any]) -> Dict[str, Any]:
    if not document.get("source_content"):
        document["source_content"] = "\n\n".join(
            chunk["text"]
            for chunk in store.knowledge_chunks()
            if chunk.get("document_id") == document["id"]
        )
        document["source_reconstructed"] = True
    else:
        document["source_reconstructed"] = False
    return document


def knowledge_reader_payload(
    document_id: str, principal: Dict[str, str]
) -> Dict[str, Any]:
    document = store.knowledge_document(document_id)
    if document is None:
        raise HTTPException(404, "知识文档不存在")
    if document["library_id"] not in ROLE_ACCESS.get(principal["role"], set()):
        raise HTTPException(403, "当前角色无权读取该知识空间")
    release_id = os.environ.get("CRA_DEMO_KB_RELEASE_ID", "").strip()
    metadata = document.get("metadata") or {}
    is_case_archive = metadata.get("source_kind") == "completed_case_archive"
    if is_case_archive and (
        document["library_id"] != "case_history"
        or not release_id
        or metadata.get("demo_release_id") != release_id
        or metadata.get("demo_only") is not True
        or metadata.get("is_mock") is not True
        or metadata.get("is_published") is not True
        or metadata.get("workflow_status") != "completed"
        or metadata.get("authority") != "derived_completed_case_not_approval"
    ):
        raise HTTPException(404, "当前演示知识集没有该文档")
    if document["library_id"] in {"proposed_rule", "evidence_spec"} and (
        not release_id
        or metadata.get("demo_release_id") != release_id
        or metadata.get("demo_only") is not True
        or metadata.get("is_published") is not True
    ):
        raise HTTPException(404, "当前演示知识集没有该文档")
    if release_id:
        if (
            metadata.get("demo_release_id") != release_id
            or metadata.get("is_published") is not True
        ):
            raise HTTPException(404, "当前演示知识集没有该文档")
    payload = knowledge_admin_payload(document)
    source_path = stored_knowledge_source(payload)
    mime_type = str(payload.get("mime_type") or "")
    payload["preview_page_count"] = 0
    if source_path and mime_type == "application/pdf":
        try:
            import pymupdf

            with pymupdf.open(source_path) as pdf:
                payload["preview_page_count"] = pdf.page_count
        except Exception:
            # The full parsed text remains readable if visual preview cannot load.
            pass
    elif source_path and mime_type.startswith("image/"):
        payload["preview_page_count"] = 1
    return payload


def stored_knowledge_source(document: Dict[str, Any]) -> Optional[Path]:
    storage_name = str((document.get("metadata") or {}).get("source_storage_name") or "")
    if not storage_name or Path(storage_name).name != storage_name:
        return None
    path = (KNOWLEDGE_SOURCES / storage_name).resolve()
    if KNOWLEDGE_SOURCES.resolve() not in path.parents or not path.is_file():
        return None
    return path


def builtin_knowledge_document_id(source: str) -> str:
    """Return a stable opaque id; source paths must never become URL path ids."""
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:24]
    return f"builtin-{digest}"


def builtin_knowledge_source_path(source: str) -> Path:
    if source.startswith("公开资料/"):
        return ROOT / "data" / "knowledge" / source.removeprefix("公开资料/")
    if source.startswith("历史申报/"):
        return ROOT / "data" / "history" / source.removeprefix("历史申报/")
    return ROOT / "data" / "policy" / source


def builtin_knowledge_title(source: str, content: str) -> str:
    heading = re.search(r"^#\s+(.+?)\s*$", content, flags=re.MULTILINE)
    if heading:
        return heading.group(1).strip()
    if source.startswith("历史申报/"):
        return "历史 CRA 申报记录"
    return Path(source).stem.replace("_", " ")


def seed_builtin_knowledge_documents() -> None:
    """Persist built-ins as complete documents plus private retrieval chunks.

    The v1 seed used filenames as document ids. Public-reference filenames carry
    a namespace slash, which works in SQL but cannot be addressed safely through
    a REST path. V2 uses deterministic opaque ids and backfills authoritative
    source text so the reader never needs to expose or reconstruct chunks.
    """
    grouped: Dict[str, List[Chunk]] = {}
    legacy_ids = set()
    for chunk in knowledge_chunks():
        grouped.setdefault(chunk.source, []).append(chunk)
        legacy_ids.add(chunk.id.split("#", 1)[0])

    current_ids = set()
    for source, chunks in grouped.items():
        document_id = builtin_knowledge_document_id(source)
        current_ids.add(document_id)
        source_path = builtin_knowledge_source_path(source)
        if source_path.is_file():
            content = source_path.read_text(encoding="utf-8-sig")
            source_filename = source_path.name
        else:
            content = "\n\n".join(chunk.text for chunk in chunks)
            source_filename = Path(source).name
        suffix = source_path.suffix.lower()
        mime_type = (
            "text/markdown"
            if suffix == ".md"
            else "text/csv"
            if suffix == ".csv"
            else "text/plain"
        )
        rows = [
            {
                "id": f"{document_id}#{index}",
                "document_id": document_id,
                "citation": chunk.citation,
                "section": chunk.section,
                "page": chunk.page,
                "text": chunk.text,
                "metadata": {"chunk_index": index, "source_kind": "builtin"},
            }
            for index, chunk in enumerate(chunks)
        ]
        store.replace_knowledge_document(
            {
                "id": document_id,
                "library_id": source_type(source),
                "title": builtin_knowledge_title(source, content),
                "citation": source,
                "source_content": content,
                "source_filename": source_filename,
                "mime_type": mime_type,
                "metadata": {
                    "managed_by": "system",
                    "parser": "builtin-utf8",
                    "source_kind": "builtin",
                    "source_reference": source,
                    "content_hash": hashlib.sha256(
                        content.encode("utf-8")
                    ).hexdigest(),
                },
            },
            rows,
        )

    for legacy_id in legacy_ids - current_ids:
        store.delete_knowledge_document(legacy_id)


def entity_candidates_for(case: Case) -> List[Dict[str, Any]]:
    result: LookupResult = entity_matcher().search(
        name=case.customer.get("name", ""),
        country=case.customer.get("country", ""),
        state=case.customer.get("state", ""),
        city=case.customer.get("city", ""),
    )
    return [
        {
            "entity_id": item.entity.entity_id,
            "name": item.entity.name,
            "country": item.entity.country,
            "state": item.entity.state,
            "city": item.entity.city,
            "address": item.entity.address,
            "postcode": item.entity.postcode,
            "rating": item.entity.rating,
            "paydex": item.entity.paydex,
            "score": round(item.score, 1),
            "reasons": item.reasons,
        }
        for item in result.candidates[:5]
    ]


def case_dict(case: Case) -> Dict[str, Any]:
    data = dict(case.__dict__)
    context_loader = getattr(store, "case_context", None)
    context = context_loader(case.case_id) if callable(context_loader) else None
    workflow = context["workflow"] if context else store.workflow(case.case_id)
    expected_task = {
        "resolve_entity": "entity_confirmation",
        "financial_signoff": "financial_signoff",
        "risk_signoff": "risk_signoff",
        "final_signoff": "final_signoff",
    }.get(workflow["current_stage"])
    tasks = (
        [task for task in context["tasks"] if task["status"] == "open"]
        if context
        else store.tasks(case.case_id, open_only=True)
    )
    if expected_task:
        tasks = [task for task in tasks if task["task_type"] == expected_task]
    elif workflow["status"] != "awaiting_human":
        tasks = []
    notes = context["notes"] if context else store.notes(case.case_id)
    if expected_task == "entity_confirmation" and not notes.get("entity_candidates"):
        notes = {**notes, "entity_candidates": entity_candidates_for(case)}
    data["workflow"] = workflow
    data["human_tasks"] = tasks
    data["events"] = context["events"] if context else store.events(case.case_id, limit=20)
    data["notes"] = notes
    data["bad_cases"] = context["bad_cases"] if context else store.bad_cases(case.case_id)
    data["stage_history"] = (
        context["stage_snapshots"]
        if context
        else store.stage_snapshots(case.case_id)
    )
    return data


def find_case(case_id: str) -> Case:
    payload = store.case(case_id)
    if payload is None:
        seeded = next((item for item in load_cases() if item.case_id == case_id), None)
        if seeded:
            store.upsert_case(seeded.__dict__)
            payload = seeded.__dict__
    if payload is None:
        raise HTTPException(404, "案件不存在")
    return Case(**payload)


def case_source_path(case: Case) -> Path:
    """Resolve a case workbook from seed samples or an imported package path."""
    candidates = [sample_path(case.case_id)]
    if case.package:
        package = Path(case.package)
        if package.is_absolute():
            candidates.insert(0, package)
        else:
            candidates.insert(0, ROOT / package)
            candidates.append(ROOT.parent.parent / package)
    if case.case_id == "CASE-004":
        candidates.append(ROOT / "data" / "handoff" / "samples" / "NEW-1000201_Northstar.xlsx")
    return next((path for path in candidates if path.is_file()), candidates[0])


def queue_financial_signoff(
    case: Case,
    values: List[Dict[str, Any]],
    *,
    source: Dict[str, Any],
) -> Dict[str, Any]:
    """把已完成的财务抽取交给人工签署；真实与演示来源使用同一道门。"""
    values = _normalize_combined_intangibles(values, case)
    notes = store.notes(case.case_id)
    _invalidate_signoffs(
        notes,
        {"financial", "risk", "score", "artifact", "final"},
        "财务证据已更新",
    )
    for derived in ("score_result", "current_artifact", "final_review"):
        notes.pop(derived, None)
    notes.update({"financial_values": values, "financial_source": source})
    store.set_notes(case.case_id, notes)
    store.open_task(
        case.case_id,
        "financial_signoff",
        "签署财务证据",
        "抽取和确定性校验已完成，请确认数值、单位和来源。",
        {"value_count": len(values), "source_type": source.get("type", "report")},
    )
    store.resolve_bad_cases(case.case_id, "financial_evidence")
    store.update_workflow(
        case.case_id,
        status="awaiting_human",
        current_stage="financial_signoff",
        current_task="等待财务证据签署",
        completed_tasks=len(values),
        total_tasks=21,
        blocker="",
    )
    store.add_event(
        case.case_id,
        "human_gate",
        "财务证据已就绪",
        f"已处理 {len(values)} 个字段，等待人工签署。",
        [source.get("label", "客户财报"), "会计字段口径"],
    )
    return case_dict(case)


def save_upload(upload: UploadFile) -> Path:
    suffix = Path(upload.filename or "upload.xlsx").suffix.lower()
    if suffix != ".xlsx":
        raise HTTPException(415, "请上传 .xlsx 格式的 CRA Excel")
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=UPLOADS) as tmp:
        shutil.copyfileobj(upload.file, tmp)
        return Path(tmp.name)


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _content_hash(payload: Any) -> str:
    """Stable fingerprint used to bind a human sign-off to one exact subject."""
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _subject_payload(case: Case, notes: Dict[str, Any], subject_type: str) -> Dict[str, Any]:
    if subject_type == "entity":
        return {"case_id": case.case_id, "selected_entity": notes.get("selected_entity") or {}}
    if subject_type == "financial":
        source = notes.get("financial_source") or {}
        return {
            "case_id": case.case_id,
            "period_end": case.new_period,
            "currency": case.statement_currency,
            "values": notes.get("financial_values") or [],
            "source": {
                key: source.get(key)
                for key in (
                    "type", "filename", "checksum", "source_period", "company",
                    "statement_scope", "validation_flags",
                )
                if source.get(key) is not None
            },
        }
    if subject_type == "risk":
        return {
            "case_id": case.case_id,
            "draft": notes.get("risk_draft") or {},
            "source_ids": [
                item.get("id") or item.get("citation")
                for item in notes.get("risk_sources") or []
            ],
        }
    if subject_type == "score":
        return {"case_id": case.case_id, "score": notes.get("score_result") or {}}
    if subject_type == "artifact":
        return {"case_id": case.case_id, "artifact": notes.get("current_artifact") or {}}
    if subject_type == "final":
        return {
            "case_id": case.case_id,
            "score": notes.get("score_result") or {},
            "artifact": notes.get("current_artifact") or {},
            "review": notes.get("final_review") or {},
        }
    raise ValueError(f"Unsupported sign-off subject: {subject_type}")


def _invalidate_signoffs(
    notes: Dict[str, Any], subject_types: set[str], reason: str
) -> None:
    signoffs = []
    for record in notes.get("signoffs") or []:
        item = dict(record)
        if item.get("subject_type") in subject_types and item.get("status") == "active":
            item.update(
                {
                    "status": "invalidated",
                    "invalidated_at": now_iso(),
                    "invalidated_reason": reason,
                }
            )
        signoffs.append(item)
    notes["signoffs"] = signoffs


def _record_signoff(
    case: Case,
    notes: Dict[str, Any],
    subject_type: str,
    principal: Dict[str, str],
    declaration: str,
) -> Dict[str, Any]:
    _invalidate_signoffs(notes, {subject_type}, "同一对象已有更新版本签署")
    existing = [
        item for item in notes.get("signoffs") or []
        if item.get("subject_type") == subject_type
    ]
    payload = _subject_payload(case, notes, subject_type)
    record = {
        "signoff_id": uuid4().hex,
        "subject_type": subject_type,
        "subject_id": f"{case.case_id}:{subject_type}",
        "subject_version": len(existing) + 1,
        "content_hash": _content_hash(payload),
        "actor": principal["user_id"],
        "actor_name": principal["name"],
        "role": principal["role"],
        "declaration": declaration,
        "status": "active",
        "signed_at": now_iso(),
    }
    notes.setdefault("signoffs", []).append(record)
    return record


def _valid_signoff(
    case: Case, notes: Dict[str, Any], subject_type: str
) -> Optional[Dict[str, Any]]:
    expected = _content_hash(_subject_payload(case, notes, subject_type))
    for record in reversed(notes.get("signoffs") or []):
        if record.get("subject_type") != subject_type:
            continue
        if record.get("status") == "active" and hmac.compare_digest(
            str(record.get("content_hash") or ""), expected
        ):
            return record
        return None
    return None


REVIEW_ROLES = {
    "高级分析师", "区域信用经理", "senior_credit_analyst",
    "credit_manager", "quality_manager",
}


def _case_access_allowed(
    case: Case,
    notes: Dict[str, Any],
    principal: Dict[str, str],
    purpose: str,
) -> bool:
    """Minimal case ACL: maintenance read, demo scope, owner/participant and review roles."""
    role = principal["role"]
    if role in {"admin", "super_admin"}:
        return purpose == "read"
    if case.mock:
        return True
    access = notes.get("case_access") or {}
    owner = str(access.get("owner_user_id") or notes.get("owner_user_id") or "")
    participants = {str(item) for item in access.get("participants") or []}
    if principal["user_id"] == owner or principal["user_id"] in participants:
        return True
    return role in REVIEW_ROLES and purpose in {"read", "final_signoff"}


def require_case_access(
    case: Case,
    principal: Dict[str, str],
    purpose: str = "read",
) -> Dict[str, Any]:
    notes = store.notes(case.case_id)
    if not _case_access_allowed(case, notes, principal, purpose):
        raise HTTPException(403, "当前用户不在该案件的授权范围内")
    return notes


def _signed_financial_values(case: Case, notes: Dict[str, Any]) -> List[ExtractedValue]:
    values = []
    normalized = _normalize_combined_intangibles(
        list(notes.get("financial_values") or []), case
    )
    for raw in normalized:
        payload = dict(raw)
        if payload.get("confidence") not in {"high", "medium", "low"}:
            payload["confidence"] = "medium"
        payload.setdefault("period_end", case.new_period)
        payload.setdefault("unit_multiplier", 1)
        payload.setdefault("currency", case.statement_currency)
        values.append(ExtractedValue.model_validate(payload))
    return values


def _normalize_combined_intangibles(
    values: List[Dict[str, Any]], case: Case
) -> List[Dict[str, Any]]:
    """Avoid double deduction when a statement reports goodwill and intangibles together."""
    if any(str(item.get("field")) == "goodwill" for item in values):
        return values
    combined = next(
        (
            item for item in values
            if str(item.get("field")) == "intangible_assets"
            and "goodwill" in str(item.get("source_text") or "").lower()
            and "intangible" in str(item.get("source_text") or "").lower()
        ),
        None,
    )
    if combined is None:
        return values
    return [
        *values,
        {
            "field": "goodwill",
            "period_end": combined.get("period_end") or case.new_period,
            "value": 0,
            "unit_multiplier": combined.get("unit_multiplier") or 1,
            "currency": combined.get("currency") or case.statement_currency,
            "page": combined.get("page"),
            "source_text": combined.get("source_text") or "",
            "confidence": "medium",
            "note": "原报表合并披露商誉与无形资产，合并数已全部记入无形资产；本列置 0 以避免额度基础重复扣减。",
        },
    ]


def _validation_flags(notes: Dict[str, Any]) -> List[Flag]:
    result = []
    for raw in (notes.get("financial_source") or {}).get("validation_flags") or []:
        result.append(
            Flag(
                str(raw.get("level") or "warn"),
                str(raw.get("field") or ""),
                str(raw.get("message") or ""),
            )
        )
    return result


def _qualitative_choices(risk_draft: Dict[str, Any]) -> Dict[str, Optional[str]]:
    result: Dict[str, Optional[str]] = {}
    for key, raw in (risk_draft.get("qualitative") or {}).items():
        if isinstance(raw, dict):
            result[key] = raw.get("choice") or raw.get("final") or raw.get("ai")
        else:
            result[key] = str(raw) if raw is not None else None
    return result


def _case_fx_rate(case: Case) -> Optional[float]:
    if case.statement_currency.upper() == "USD":
        return 1.0
    rates = case_fx(case)
    if not rates and case.mock:
        rates = load_fx()
    exact = rates.get((case.statement_currency, case.new_period))
    if exact:
        return float(exact)
    candidates = [
        (period, value)
        for (currency, period), value in rates.items()
        if currency == case.statement_currency
    ]
    return float(sorted(candidates, reverse=True)[0][1]) if candidates else None


def _header_text(value: Any) -> str:
    return value if isinstance(value, str) else ""


async def materialize_managed_upload(
    upload: Optional[UploadFile],
    *,
    managed_id: str,
    managed_url: str,
    managed_path: str,
    managed_name: str,
    managed_size: str,
    managed_sha256: str,
    allowed_suffixes: set[str],
    max_bytes: int,
    default_name: str,
) -> tuple[Path, str, str]:
    """Return a verified local cache path for multipart or managed-object input."""
    managed_id = _header_text(managed_id)
    managed_url = _header_text(managed_url)
    managed_path = _header_text(managed_path)
    managed_name = _header_text(managed_name)
    managed_size = _header_text(managed_size)
    managed_sha256 = _header_text(managed_sha256)
    filename = Path(upload.filename or default_name).name if upload else Path(
        unquote(managed_name) or default_name
    ).name
    suffix = Path(filename).suffix.lower()
    if suffix not in allowed_suffixes:
        allowed = "、".join(sorted(allowed_suffixes))
        raise HTTPException(415, f"仅支持 {allowed} 格式")

    if upload is not None:
        target = MANAGED_CACHE / f"legacy-{uuid4().hex}{suffix}"
        size = 0
        digest = hashlib.sha256()
        with target.open("wb") as output:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    target.unlink(missing_ok=True)
                    raise HTTPException(413, "文件超过允许的大小上限")
                digest.update(chunk)
                output.write(chunk)
        if size == 0:
            target.unlink(missing_ok=True)
            raise HTTPException(422, "上传的文件是空文件")
        return target, filename, digest.hexdigest()

    if not managed_id or not re.fullmatch(r"[0-9a-fA-F-]{32,36}", managed_id):
        raise HTTPException(422, "缺少有效的托管文件 ID")
    target = MANAGED_CACHE / f"{managed_id}{suffix}"

    if managed_path:
        source = Path(managed_path).resolve()
        managed_root = (RUNTIME / "managed-files").resolve()
        if managed_root not in source.parents or not source.is_file():
            raise HTTPException(422, "本地托管文件路径无效")
        target = source
    elif managed_url:
        parsed = urlparse(managed_url)
        allowed_local = parsed.scheme == "http" and parsed.hostname in {
            "127.0.0.1", "localhost"
        }
        if parsed.scheme != "https" and not allowed_local:
            raise HTTPException(422, "对象存储下载地址必须使用 HTTPS")
        temporary = MANAGED_CACHE / f".{managed_id}-{uuid4().hex}.part"
        size = 0
        try:
            async with httpx.AsyncClient(follow_redirects=False, timeout=60.0) as client:
                async with client.stream("GET", managed_url) as response:
                    if response.status_code != 200:
                        raise HTTPException(502, "对象存储读取失败")
                    with temporary.open("wb") as output:
                        async for chunk in response.aiter_bytes(1024 * 1024):
                            size += len(chunk)
                            if size > max_bytes:
                                raise HTTPException(413, "文件超过允许的大小上限")
                            output.write(chunk)
            if size == 0:
                raise HTTPException(422, "上传的文件是空文件")
            temporary.replace(target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    else:
        raise HTTPException(422, "文件对象没有可读取的位置")

    actual_size = target.stat().st_size
    if actual_size <= 0:
        raise HTTPException(422, "上传的文件是空文件")
    if actual_size > max_bytes:
        raise HTTPException(413, "文件超过允许的大小上限")
    if managed_size and managed_size.isdigit() and actual_size != int(managed_size):
        raise HTTPException(422, "文件大小校验失败")
    actual_sha256 = _sha256_path(target)
    if managed_sha256 and not hmac.compare_digest(actual_sha256, managed_sha256.lower()):
        raise HTTPException(422, "文件 SHA-256 校验失败")
    return target, filename, actual_sha256


def materialize_case_source(
    case: Case,
    *,
    source_id: str,
    source_url: str,
    source_path: str,
    source_name: str,
    source_size: str,
    source_sha256: str,
) -> None:
    source_id = _header_text(source_id)
    if not source_id:
        return
    path, _, _ = asyncio.run(
        materialize_managed_upload(
            None,
            managed_id=source_id,
            managed_url=_header_text(source_url),
            managed_path=_header_text(source_path),
            managed_name=_header_text(source_name),
            managed_size=_header_text(source_size),
            managed_sha256=_header_text(source_sha256),
            allowed_suffixes={".xlsx"},
            max_bytes=20 * 1024 * 1024,
            default_name="case.xlsx",
        )
    )
    case.package = str(path)


def package_payload(path: Path) -> Dict[str, Any]:
    pkg = read_package(path)
    existing = next(
        (item for item in load_cases() if item.bp == pkg.case.get("BP")), None
    )
    return {
        "filename": path.name,
        "checksum": hashlib.sha256(path.read_bytes()).hexdigest(),
        "valid": not pkg.errors,
        "errors": pkg.errors,
        "meta": pkg.meta,
        "case": pkg.case,
        "years": [
            {
                "period_end": period,
                "switch": switch,
                "source": "当期待填" if switch == "No - Enter Manually" else "SAP 交接",
            }
            for period, switch in pkg.years
        ],
        "sap_periods": list(pkg.sap.keys()),
        "fx_count": len(pkg.fx),
        "ar": pkg.ar,
        "duplicate": (
            {"case_id": existing.case_id, "title": existing.title}
            if existing
            else None
        ),
    }


def ensure_seed_states() -> None:
    # Seed fixtures once. Runtime GET requests must never perform this work, and
    # later API restarts should treat PostgreSQL as the source of truth.
    if store.has_data_migration("case-seed-v2") and store.has_data_migration(
        "knowledge-seed-v2"
    ):
        return
    presets = {
        "CASE-001": (
            "running",
            "financial_evidence",
            "校验 2025 年财务证据",
            12,
            21,
        ),
        "CASE-002": (
            "awaiting_human",
            "resolve_entity",
            "等待分析师确认企业主体",
            1,
            6,
        ),
        "CASE-003": (
            "blocked",
            "financial_evidence",
            "处理扫描件中的数量级冲突",
            16,
            21,
        ),
        "CASE-004": (
            "blocked",
            "financial_evidence",
            "等待上传当期财务报告",
            1,
            21,
        ),
    }
    seeded_cases = load_cases()
    for case in seeded_cases:
        store.upsert_case(case.__dict__)
    migrate_legacy = getattr(store, "migrate_legacy_sqlite", None)
    if callable(migrate_legacy):
        migrate_legacy(RUNTIME / "workbench.db")

    for case in seeded_cases:
        preset = presets.get(
            case.case_id,
            ("ready", "resolve_entity", "核对企业主体候选", 1, 6),
        )
        store.ensure_case(
            case.case_id,
            status=preset[0],
            stage=preset[1],
            task=preset[2],
            completed_tasks=preset[3],
            total_tasks=preset[4],
        )

    if not store.has_data_migration("knowledge-seed-v2"):
        seed_builtin_knowledge_documents()
        store.mark_data_migration("knowledge-seed-v1")
        store.mark_data_migration("knowledge-seed-v2")
    if not store.events("CASE-001"):
        store.add_event(
            "CASE-001",
            "tool_completed",
            "CRA Excel 已导入",
            "已读取 BP、信用段、历史财务、汇率和付款表现。",
            ["Financial Statements", "Header params", "汇率快照"],
        )
        store.add_event(
            "CASE-001",
            "agent_progress",
            "已定位财务报表",
            "Agent 正在核对抽取数字的单位、页码与会计平衡。",
            ["客户财报", "会计字段口径"],
        )
    if not store.tasks("CASE-002"):
        store.open_task(
            "CASE-002",
            "entity_confirmation",
            "确认企业主体",
            "候选企业未达到自动绑定条件，请核对名称、地址和财务规模。",
        )
    case_004_notes = store.notes("CASE-004")
    if not case_004_notes.get("financial_values"):
        for task in store.tasks("CASE-004", open_only=True):
            if task["task_type"] == "financial_signoff":
                store.resolve_task("CASE-004", task["id"])
        store.update_workflow(
            "CASE-004",
            status="blocked",
            current_stage="financial_evidence",
            current_task="等待上传当期财务报告",
            blocker="缺少当期财务报告或已标注的演示抽取结果。",
            completed_tasks=1,
            total_tasks=21,
        )
        store.add_bad_case(
            "CASE-004",
            node="financial_evidence",
            failure_type="当期财务证据缺失",
            exposure="L1",
            impact="medium",
            priority="P2",
            detection_source="材料完整性校验",
            response="阻断签署并要求上传当期财务报告",
        )
    elif not store.tasks("CASE-004", open_only=True):
        store.open_task(
            "CASE-004",
            "financial_signoff",
            "签署财务证据",
            "21 个字段已完成校验，请确认证据版本。",
        )
    if not store.bad_cases("CASE-003"):
        store.add_bad_case(
            "CASE-003",
            node="financial_evidence",
            failure_type="扫描件数量级与原页冲突",
            exposure="L1",
            impact="medium",
            priority="P2",
            detection_source="确定性数量级校验",
            response="自动阻断回填，要求人工核对原页与单位",
        )
    store.mark_data_migration("case-seed-v2")


def seed_in_background() -> None:
    try:
        ensure_seed_states()
        # This also runs when the seed migration was already completed.
        sync_all_case_evidence(store)
    except Exception:
        # The database-backed API must stay available even if optional demo
        # fixture initialization encounters a temporarily unavailable Neon DB.
        logging.getLogger(__name__).exception("CRA demo seed initialization failed")


def sync_existing_cases_in_background() -> None:
    try:
        sync_all_case_evidence(store)
    except Exception:
        logging.getLogger(__name__).exception("CRA case knowledge reconciliation failed")


@app.on_event("startup")
def startup() -> None:
    # Seeding is idempotent but may need to wake a suspended remote database.
    # Do not make all reads wait for that maintenance work to finish.
    if os.environ.get("CRA_AUTO_SEED", "true").lower() in {"1", "true", "yes"}:
        Thread(target=seed_in_background, name="cra-demo-seed", daemon=True).start()
    else:
        Thread(target=sync_existing_cases_in_background, name="cra-case-index", daemon=True).start()
    from cra.retrieve.vector import is_available as vector_stack_available

    settings = get_settings()
    if (settings.rag_use_vector or settings.rag_use_rerank) and not vector_stack_available():
        logging.getLogger(__name__).warning(
            "RAG semantic retrieval is enabled but ML dependencies are missing; "
            "install requirements-ml.txt. Keyword retrieval remains available."
        )


@app.get("/health")
def health() -> Dict[str, Any]:
    settings = get_settings()
    from cra.retrieve.vector import is_available as vector_stack_available

    ml_available = vector_stack_available()
    return {
        "status": "ok",
        "service": "cra-agent",
        "cases": len(store.cases()),
        "database_backend": store.backend,
        "database_warning": store.startup_warning,
        "demo_kb_release_id": os.environ.get("CRA_DEMO_KB_RELEASE_ID", "").strip(),
        "llm_configured": bool(settings.deepseek_api_key),
        "rag": {
            "vector_configured": settings.rag_use_vector,
            "rerank_configured": settings.rag_use_rerank,
            "ml_dependencies_available": ml_available,
            "vector_active": settings.rag_use_vector and ml_available,
            "rerank_active": settings.rag_use_rerank and ml_available,
        },
    }


@app.get("/knowledge")
def visible_knowledge(
    x_cra_user: str = Header(default="", alias="X-CRA-User"),
    x_cra_name: str = Header(default="", alias="X-CRA-Name"),
    x_cra_role: str = Header(default="", alias="X-CRA-Role"),
) -> Dict[str, Any]:
    principal = require_request_actor(x_cra_user, x_cra_name, x_cra_role)
    catalog = knowledge_catalog(
        store.knowledge_documents(), principal,
        os.environ.get("CRA_DEMO_KB_RELEASE_ID", "").strip(),
    )
    catalog["can_manage"] = principal["role"] in {"admin", "super_admin"}
    return catalog


@app.get("/knowledge/documents/{document_id}")
def knowledge_document_detail(
    document_id: str,
    x_cra_user: str = Header(default="", alias="X-CRA-User"),
    x_cra_name: str = Header(default="", alias="X-CRA-Name"),
    x_cra_role: str = Header(default="", alias="X-CRA-Role"),
) -> Dict[str, Any]:
    principal = require_request_actor(x_cra_user, x_cra_name, x_cra_role)
    return knowledge_reader_payload(document_id, principal)


@app.get("/knowledge/documents/{document_id}/source")
def knowledge_document_source(
    document_id: str,
    x_cra_user: str = Header(default="", alias="X-CRA-User"),
    x_cra_name: str = Header(default="", alias="X-CRA-Name"),
    x_cra_role: str = Header(default="", alias="X-CRA-Role"),
):
    principal = require_request_actor(x_cra_user, x_cra_name, x_cra_role)
    document = knowledge_reader_payload(document_id, principal)
    filename = Path(document.get("source_filename") or f"{document_id}.txt").name
    safe_filename = filename.replace('"', "_").replace("\r", "_").replace("\n", "_")
    source_path = stored_knowledge_source(document)
    if source_path:
        return FileResponse(
            source_path,
            media_type=document.get("mime_type") or "application/octet-stream",
            filename=filename,
            content_disposition_type="inline",
        )
    return Response(
        str(document.get("source_content") or ""),
        media_type="text/plain",
        headers={
            "Content-Disposition": (
                f"inline; filename*=UTF-8''{quote(safe_filename, safe='')}"
            )
        },
    )


@app.get("/knowledge/documents/{document_id}/preview")
async def knowledge_document_preview(
    document_id: str,
    page: int = 1,
    x_cra_user: str = Header(default="", alias="X-CRA-User"),
    x_cra_name: str = Header(default="", alias="X-CRA-Name"),
    x_cra_role: str = Header(default="", alias="X-CRA-Role"),
    x_cra_source_id: str = Header(default="", alias="X-CRA-Knowledge-Source-Id"),
    x_cra_source_url: str = Header(default="", alias="X-CRA-Knowledge-Source-Url"),
    x_cra_source_path: str = Header(default="", alias="X-CRA-Knowledge-Source-Path"),
    x_cra_source_name: str = Header(default="", alias="X-CRA-Knowledge-Source-Name"),
    x_cra_source_size: str = Header(default="", alias="X-CRA-Knowledge-Source-Size"),
    x_cra_source_sha256: str = Header(default="", alias="X-CRA-Knowledge-Source-Sha256"),
):
    """Return a browser-stable image preview for uploaded PDFs and images."""
    principal = require_request_actor(x_cra_user, x_cra_name, x_cra_role)
    document = knowledge_reader_payload(document_id, principal)
    x_cra_source_id = _header_text(x_cra_source_id)
    if x_cra_source_id:
        source_path, _, _ = await materialize_managed_upload(
            None,
            managed_id=x_cra_source_id,
            managed_url=x_cra_source_url,
            managed_path=x_cra_source_path,
            managed_name=x_cra_source_name,
            managed_size=x_cra_source_size,
            managed_sha256=x_cra_source_sha256,
            allowed_suffixes=set(KNOWLEDGE_EXTENSIONS),
            max_bytes=MAX_KNOWLEDGE_UPLOAD_BYTES,
            default_name=str(document.get("source_filename") or "knowledge.pdf"),
        )
    else:
        source_path = stored_knowledge_source(document)
    if not source_path:
        raise HTTPException(404, "该文档没有可预览的源文件")

    mime_type = str(document.get("mime_type") or "application/octet-stream")
    if mime_type.startswith("image/"):
        if page != 1:
            raise HTTPException(404, "图片只有 1 页")
        return FileResponse(
            source_path,
            media_type=mime_type,
            filename=Path(document.get("source_filename") or source_path.name).name,
            content_disposition_type="inline",
        )
    if mime_type != "application/pdf":
        raise HTTPException(415, "该文件类型不支持图像预览")

    try:
        import pymupdf

        with pymupdf.open(source_path) as pdf:
            if page < 1 or page > pdf.page_count:
                raise HTTPException(404, f"PDF 只有 {pdf.page_count} 页")
            pdf_page = pdf.load_page(page - 1)
            matrix = pymupdf.Matrix(1.6, 1.6)
            preview = pdf_page.get_pixmap(matrix=matrix, alpha=False).tobytes("png")
            page_count = pdf.page_count
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422, "PDF 页面预览生成失败") from exc

    return Response(
        content=preview,
        media_type="image/png",
        headers={
            "Content-Disposition": f'inline; filename="{document_id}-page-{page}.png"',
            "X-Preview-Page": str(page),
            "X-Preview-Page-Count": str(page_count),
        },
    )


@app.post("/knowledge/documents", status_code=201)
async def upload_knowledge_document(
    library_id: Optional[str] = Form(default=None),
    title: Optional[str] = Form(default=None),
    file: Optional[UploadFile] = File(default=None),
    x_cra_user: str = Header(default="", alias="X-CRA-User"),
    x_cra_name: str = Header(default="", alias="X-CRA-Name"),
    x_cra_role: str = Header(default="", alias="X-CRA-Role"),
    x_cra_file_id: str = Header(default="", alias="X-CRA-File-Id"),
    x_cra_file_url: str = Header(default="", alias="X-CRA-File-Url"),
    x_cra_file_path: str = Header(default="", alias="X-CRA-File-Path"),
    x_cra_file_name: str = Header(default="", alias="X-CRA-File-Name"),
    x_cra_file_size: str = Header(default="", alias="X-CRA-File-Size"),
    x_cra_file_sha256: str = Header(default="", alias="X-CRA-File-Sha256"),
    x_cra_library_id: str = Header(default="", alias="X-CRA-Library-Id"),
    x_cra_document_title: str = Header(default="", alias="X-CRA-Document-Title"),
) -> Dict[str, Any]:
    principal = require_knowledge_admin(x_cra_user, x_cra_name, x_cra_role)
    x_cra_file_id = _header_text(x_cra_file_id)
    library_id = (_header_text(x_cra_library_id) or library_id or "").strip()
    title = unquote(_header_text(x_cra_document_title) or title or "").strip()
    if library_id not in LIBRARY_PREFIX:
        raise HTTPException(422, "请选择有效的知识库")
    if not title or len(title) > 160:
        raise HTTPException(422, "文档标题需为 1至 160 个字符")
    source_cache, filename, source_hash = await materialize_managed_upload(
        file,
        managed_id=x_cra_file_id,
        managed_url=x_cra_file_url,
        managed_path=x_cra_file_path,
        managed_name=x_cra_file_name,
        managed_size=x_cra_file_size,
        managed_sha256=x_cra_file_sha256,
        allowed_suffixes=set(KNOWLEDGE_EXTENSIONS),
        max_bytes=MAX_KNOWLEDGE_UPLOAD_BYTES,
        default_name="knowledge.md",
    )
    payload = source_cache.read_bytes()
    suffix = Path(filename).suffix.lower()

    try:
        parsed = parse_knowledge_source(payload, filename)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    content = parsed.content.strip()
    if not content:
        raise HTTPException(422, "文档解析后没有可用于检索的文字")
    if len(content) > 2_000_000:
        raise HTTPException(413, "解析后文本超过 200 万字符上限")

    document_id = f"rag-{uuid4().hex}"
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    provenance = {
        "source_sha256": source_hash,
        "content_hash": content_hash,
        "parser": parsed.parser,
        "ocr_backend": parsed.ocr_backend,
        "ocr_page_numbers": list(parsed.ocr_page_numbers),
        "ocr_requires_review": bool(parsed.ocr_page_numbers),
        "page_count": len(parsed.pages) if parsed.pages is not None else None,
    }
    chunks = knowledge_rows(
        document_id,
        library_id,
        title,
        content,
        pages=parsed.pages,
        metadata=provenance,
    )
    storage_name = f"{document_id}{suffix}"
    source_path = KNOWLEDGE_SOURCES / storage_name
    source_path.write_bytes(payload)
    try:
        document = store.replace_knowledge_document(
            {
                "id": document_id,
                "library_id": library_id,
                "title": title,
                "citation": knowledge_source(library_id, title),
                "source_content": content,
                "source_filename": filename,
                "mime_type": parsed.mime_type,
                "metadata": {
                    "managed_by": "admin",
                    "created_by": principal["user_id"],
                    "updated_by": principal["user_id"],
                    "parser": parsed.parser,
                    "source_storage_name": storage_name,
                    "source_file_id": x_cra_file_id,
                    "source_size": len(payload),
                    **provenance,
                },
            },
            chunks,
        )
    except Exception:
        source_path.unlink(missing_ok=True)
        raise
    return knowledge_admin_payload(document)


@app.put("/knowledge/documents/{document_id}")
def update_knowledge_document(
    document_id: str,
    body: KnowledgeDocumentUpdate,
    x_cra_user: str = Header(default="", alias="X-CRA-User"),
    x_cra_name: str = Header(default="", alias="X-CRA-Name"),
    x_cra_role: str = Header(default="", alias="X-CRA-Role"),
) -> Dict[str, Any]:
    principal = require_knowledge_admin(x_cra_user, x_cra_name, x_cra_role)
    existing = store.knowledge_document(document_id)
    if existing is None:
        raise HTTPException(404, "知识文档不存在")
    if (existing.get("library_id") not in LIBRARY_PREFIX
            or (existing.get("metadata") or {}).get("managed_by") == "case_knowledge.py"):
        raise HTTPException(403, "该演示知识由受控发布脚本维护，不能在页面直接修改")
    title = body.title.strip()
    if not title:
        raise HTTPException(422, "文档标题不能只包含空格")
    content = body.content.strip()
    metadata = dict(existing.get("metadata") or {})
    metadata.update(
        {
            "managed_by": "admin",
            "updated_by": principal["user_id"],
            "source_text_edited": True,
            "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }
    )
    chunks = knowledge_rows(
        document_id, body.library_id, title, content,
        metadata={key: metadata.get(key) for key in (
            "source_sha256", "content_hash", "parser", "ocr_backend",
            "ocr_page_numbers", "ocr_requires_review", "page_count",
            "source_text_edited",
        )},
    )
    document = store.replace_knowledge_document(
        {
            "id": document_id,
            "library_id": body.library_id,
            "title": title,
            "citation": knowledge_source(body.library_id, title),
            "source_content": content,
            "source_filename": existing.get("source_filename") or "",
            "mime_type": existing.get("mime_type") or "text/markdown",
            "metadata": metadata,
        },
        chunks,
    )
    return knowledge_admin_payload(document)


@app.delete("/knowledge/documents/{document_id}")
def delete_knowledge_document(
    document_id: str,
    x_cra_user: str = Header(default="", alias="X-CRA-User"),
    x_cra_name: str = Header(default="", alias="X-CRA-Name"),
    x_cra_role: str = Header(default="", alias="X-CRA-Role"),
) -> Dict[str, Any]:
    principal = require_knowledge_admin(x_cra_user, x_cra_name, x_cra_role)
    existing = store.knowledge_document(document_id)
    if (existing is None or existing.get("library_id") not in LIBRARY_PREFIX
            or (existing.get("metadata") or {}).get("managed_by") == "case_knowledge.py"):
        raise HTTPException(404, "知识文档不存在")
    deleted = store.delete_knowledge_document(
        document_id,
        audit={
            "action": "delete",
            "library_id": existing.get("library_id") or "",
            "title": existing.get("title") or "",
            "source_filename": existing.get("source_filename") or "",
            "chunk_count": int(existing.get("chunk_count") or 0),
            "actor_user_id": principal["user_id"],
            "actor_name": principal["name"],
            "actor_role": principal["role"],
            "snapshot": {
                "citation": existing.get("citation") or "",
                "mime_type": existing.get("mime_type") or "",
                "metadata": existing.get("metadata") or {},
            },
        },
    )
    if not deleted:
        raise HTTPException(404, "知识文档不存在")
    if existing:
        source_path = stored_knowledge_source(existing)
        if source_path:
            source_path.unlink(missing_ok=True)
    return {
        "deleted": True,
        "document_id": document_id,
        "audit_recorded": True,
    }


@app.get("/cases")
def cases(
    x_cra_user: str = Header(default="", alias="X-CRA-User"),
    x_cra_name: str = Header(default="", alias="X-CRA-Name"),
    x_cra_role: str = Header(default="", alias="X-CRA-Role"),
) -> List[Dict[str, Any]]:
    principal = require_request_actor(x_cra_user, x_cra_name, x_cra_role)
    visible = []
    for summary in store.case_summaries():
        payload = store.case(summary["case_id"])
        if payload is None:
            continue
        case = Case(**payload)
        if _case_access_allowed(case, store.notes(case.case_id), principal, "read"):
            visible.append(summary)
    return visible


@app.get("/cases/{case_id}")
def case_detail(
    case_id: str,
    x_cra_user: str = Header(default="", alias="X-CRA-User"),
    x_cra_name: str = Header(default="", alias="X-CRA-Name"),
    x_cra_role: str = Header(default="", alias="X-CRA-Role"),
) -> Dict[str, Any]:
    principal = require_request_actor(x_cra_user, x_cra_name, x_cra_role)
    case = find_case(case_id)
    require_case_access(case, principal)
    return case_dict(case)


@app.post("/cases/{case_id}/evidence/reindex")
def reindex_case_evidence(
    case_id: str,
    x_cra_user: str = Header(default="", alias="X-CRA-User"),
    x_cra_name: str = Header(default="", alias="X-CRA-Name"),
    x_cra_role: str = Header(default="", alias="X-CRA-Role"),
) -> Dict[str, Any]:
    """Repair a case projection after a writer outside the Python store commits."""
    principal = require_business_actor(x_cra_user, x_cra_name, x_cra_role)
    case = find_case(case_id)
    require_case_access(case, principal, "write")
    changed = sync_case_evidence(store, case_id)
    return {
        "case_id": case_id, "document_id": case_evidence_document_id(case_id),
        "changed": changed, "knowledge_revision": store.knowledge_revision(),
    }


@app.patch("/cases/{case_id}")
def update_case_title(
    case_id: str,
    body: CaseTitleUpdate,
    x_cra_user: str = Header(default="", alias="X-CRA-User"),
    x_cra_name: str = Header(default="", alias="X-CRA-Name"),
    x_cra_role: str = Header(default="", alias="X-CRA-Role"),
) -> Dict[str, Any]:
    principal = require_business_actor(x_cra_user, x_cra_name, x_cra_role)
    case = find_case(case_id)
    require_case_access(case, principal, "write")
    title = body.title.strip()
    if len(title) < 2:
        raise HTTPException(422, "案件名称至少需要 2 个字符")
    if title == case.title:
        return case_dict(case)
    previous_title = case.title
    store.update_case_title(case_id, title)
    store.add_event(
        case_id,
        "case_updated",
        "案件名称已修改",
        f"{principal['name']} 将案件名称从「{previous_title}」修改为「{title}」。",
        [],
    )
    return case_dict(find_case(case_id))


@app.post("/cases/preview")
async def preview_case(
    file: Optional[UploadFile] = File(default=None),
    x_cra_file_id: str = Header(default="", alias="X-CRA-File-Id"),
    x_cra_file_url: str = Header(default="", alias="X-CRA-File-Url"),
    x_cra_file_path: str = Header(default="", alias="X-CRA-File-Path"),
    x_cra_file_name: str = Header(default="", alias="X-CRA-File-Name"),
    x_cra_file_size: str = Header(default="", alias="X-CRA-File-Size"),
    x_cra_file_sha256: str = Header(default="", alias="X-CRA-File-Sha256"),
    x_cra_user: str = Header(default="", alias="X-CRA-User"),
    x_cra_name: str = Header(default="", alias="X-CRA-Name"),
    x_cra_role: str = Header(default="", alias="X-CRA-Role"),
) -> Dict[str, Any]:
    require_business_actor(x_cra_user, x_cra_name, x_cra_role)
    path, filename, _ = await materialize_managed_upload(
        file,
        managed_id=x_cra_file_id,
        managed_url=x_cra_file_url,
        managed_path=x_cra_file_path,
        managed_name=x_cra_file_name,
        managed_size=x_cra_file_size,
        managed_sha256=x_cra_file_sha256,
        allowed_suffixes={".xlsx"},
        max_bytes=20 * 1024 * 1024,
        default_name="case.xlsx",
    )
    result = package_payload(path)
    result["filename"] = filename
    return result


@app.post("/cases")
async def create_case(
    file: Optional[UploadFile] = File(default=None),
    owner: str = Form("张俊"),
    due_date: str = Form(""),
    duplicate_mode: str = Form("review"),
    x_cra_user: str = Header(default="", alias="X-CRA-User"),
    x_cra_name: str = Header(default="", alias="X-CRA-Name"),
    x_cra_role: str = Header(default="", alias="X-CRA-Role"),
    x_cra_file_id: str = Header(default="", alias="X-CRA-File-Id"),
    x_cra_file_url: str = Header(default="", alias="X-CRA-File-Url"),
    x_cra_file_path: str = Header(default="", alias="X-CRA-File-Path"),
    x_cra_file_name: str = Header(default="", alias="X-CRA-File-Name"),
    x_cra_file_size: str = Header(default="", alias="X-CRA-File-Size"),
    x_cra_file_sha256: str = Header(default="", alias="X-CRA-File-Sha256"),
    x_cra_duplicate_mode: str = Header(default="", alias="X-CRA-Duplicate-Mode"),
) -> Dict[str, Any]:
    principal = require_business_actor(x_cra_user, x_cra_name, x_cra_role)
    x_cra_file_id = _header_text(x_cra_file_id)
    duplicate_mode = _header_text(x_cra_duplicate_mode) or duplicate_mode
    path, _, _ = await materialize_managed_upload(
        file,
        managed_id=x_cra_file_id,
        managed_url=x_cra_file_url,
        managed_path=x_cra_file_path,
        managed_name=x_cra_file_name,
        managed_size=x_cra_file_size,
        managed_sha256=x_cra_file_sha256,
        allowed_suffixes={".xlsx"},
        max_bytes=20 * 1024 * 1024,
        default_name="case.xlsx",
    )
    preview = package_payload(path)
    if not preview["valid"]:
        raise HTTPException(422, detail=preview["errors"])
    if duplicate_mode not in {"review", "update", "cycle"}:
        raise HTTPException(422, "重复案件处理方式无效")
    if preview["duplicate"] and duplicate_mode == "review":
        raise HTTPException(
            409,
            detail={
                "message": "该 BP 已有案件，请明确选择更新或新评审周期。",
                "existing": preview["duplicate"],
            },
        )
    previous_notes: Dict[str, Any] = {}
    if preview["duplicate"] and duplicate_mode == "update":
        existing_case_id = str(preview["duplicate"]["case_id"])
        existing_case = find_case(existing_case_id)
        require_case_access(existing_case, principal, "write")
        existing_workflow = store.workflow(existing_case_id)
        if existing_workflow["status"] == "completed":
            raise HTTPException(
                409,
                "已完成案件不能被新文件覆盖，请选择“开始新评审周期”。",
            )
        previous_notes = store.notes(existing_case_id)
    case, created, errors = import_package(path, force_new=duplicate_mode == "cycle")
    if errors or case is None:
        raise HTTPException(422, detail=errors)
    store.upsert_case(case.__dict__, overwrite=True)
    if created:
        store.ensure_case(
            case.case_id,
            status="running",
            stage="resolve_entity",
            task="搜索并排序企业主体候选",
            completed_tasks=1,
            total_tasks=6,
        )
    else:
        # Replacing a workbook invalidates entity binding and every downstream
        # signoff/result. Keep snapshots and cancelled tasks as audit history,
        # but never inherit the old workflow position.
        store.reset_case_review(case.case_id)
    owner_name = previous_notes.get("owner") or principal["name"]
    owner_user_id = previous_notes.get("owner_user_id") or principal["user_id"]
    owner_role = previous_notes.get("owner_role") or principal["role"]
    case_access = previous_notes.get("case_access") or {
        "tenant": "demo" if case.mock else "default",
        "region": str(case.customer.get("country") or ""),
        "owner_user_id": owner_user_id,
        "participants": [],
        "review_roles": sorted(REVIEW_ROLES),
    }
    store.set_notes(
        case.case_id,
        {
            # Updating an existing draft preserves ownership and access scope,
            # while intentionally dropping all evidence, signoffs and outputs.
            "owner": owner_name,
            "owner_user_id": owner_user_id,
            "owner_role": owner_role,
            "case_access": case_access,
            "due_date": due_date or previous_notes.get("due_date", ""),
            "source_file_id": x_cra_file_id,
        },
    )
    store.add_event(
        case.case_id,
        "case_created" if created else "case_updated",
        "案件已创建" if created else "案件已更新",
        (
            "已保存 Excel 快照并启动企业主体核验。"
            if created
            else "已替换 Excel，取消旧待办并重新启动企业主体核验。"
        ),
        ["CRA Excel"],
    )
    return case_dict(case)


@app.post("/cases/{case_id}/agent/run")
def run_agent(
    case_id: str,
    action: AgentAction,
    x_cra_user: str = Header(default="", alias="X-CRA-User"),
    x_cra_name: str = Header(default="", alias="X-CRA-Name"),
    x_cra_role: str = Header(default="", alias="X-CRA-Role"),
    x_cra_source_id: str = Header(default="", alias="X-CRA-Case-Source-Id"),
    x_cra_source_url: str = Header(default="", alias="X-CRA-Case-Source-Url"),
    x_cra_source_path: str = Header(default="", alias="X-CRA-Case-Source-Path"),
    x_cra_source_name: str = Header(default="", alias="X-CRA-Case-Source-Name"),
    x_cra_source_size: str = Header(default="", alias="X-CRA-Case-Source-Size"),
    x_cra_source_sha256: str = Header(default="", alias="X-CRA-Case-Source-Sha256"),
) -> Dict[str, Any]:
    principal = require_business_actor(x_cra_user, x_cra_name, x_cra_role)
    case = find_case(case_id)
    require_case_access(case, principal, "write")
    materialize_case_source(
        case,
        source_id=x_cra_source_id,
        source_url=x_cra_source_url,
        source_path=x_cra_source_path,
        source_name=x_cra_source_name,
        source_size=x_cra_source_size,
        source_sha256=x_cra_source_sha256,
    )
    workflow = store.workflow(case_id)
    stage = workflow["current_stage"]

    if stage == "resolve_entity":
        candidates = entity_candidates_for(case)
        store.set_notes(case_id, {**store.notes(case_id), "entity_candidates": candidates})
        store.open_task(
            case_id,
            "entity_confirmation",
            "确认企业主体",
            "Agent 已完成候选排序。合并前请核对名称、地址、国家和规模。",
            {"candidates": candidates},
        )
        store.update_workflow(
            case_id,
            status="awaiting_human",
            current_task="等待分析师确认企业主体",
            completed_tasks=len(candidates),
            total_tasks=max(len(candidates), 1),
        )
        store.add_event(
            case_id,
            "human_gate",
            "企业主体候选已就绪",
            f"找到 {len(candidates)} 个候选，系统不会自动 Merge。",
            ["企业实体库"],
        )
    elif stage == "financial_evidence":
        extraction = ROOT / "data" / "samples" / f"extraction_{case_id}.json"
        if not extraction.exists():
            store.add_bad_case(
                case_id,
                node="financial_evidence",
                failure_type="当期财务证据缺失",
                exposure="L1",
                impact="medium",
                priority="P2",
                detection_source="材料完整性校验",
                response="阻断执行并要求上传当期财务报告",
            )
            store.update_workflow(
                case_id,
                status="blocked",
                blocker="缺少当期财务报告或已标注的演示抽取结果。",
                current_task="等待上传当期财务报告",
            )
            store.add_event(
                case_id,
                "blocked",
                "财务证据流程已阻断",
                "当前案件没有可用的财务报告。",
            )
        else:
            data = json.loads(extraction.read_text(encoding="utf-8"))
            values = data.get("llm_output", {}).get("values", [])
            return queue_financial_signoff(
                case,
                values,
                source={"type": "report", "label": "客户财报"},
            )
    elif stage == "retrieve_context":
        retriever = retriever_for(principal, case=case)
        note = store.notes(case_id)
        from cra.llm import get_llm

        package = generate_risk_opinion(case, note, retriever, get_llm())
        sources = package["sources"]
        note["risk_sources"] = sources
        note["risk_draft"] = {**package["draft"], "updated_at": now_iso()}
        note["risk_opinion_versions"] = [
            {
                "version": 1,
                "editor": "Agent",
                "saved_at": note["risk_draft"]["updated_at"],
                "company_description": note["risk_draft"]["company_description"],
                "risk_comments": note["risk_draft"]["risk_comments"],
            }
        ]
        store.set_notes(case_id, note)
        store.open_task(
            case_id,
            "risk_signoff",
            "确认风险意见",
            "Agent 已按当前规则检索并生成可编辑草稿。",
            {"sources": sources},
        )
        store.update_workflow(
            case_id,
            status="awaiting_human",
            current_stage="risk_signoff",
            current_task="等待风险意见签署",
            completed_tasks=len(sources),
            total_tasks=max(len(sources), 1),
        )
        store.add_event(
            case_id,
            "human_gate",
            "风险草稿已就绪",
            f"草稿引用 {len(sources)} 条规则或公开资料，等待人工复核。",
            [item["citation"] for item in sources],
        )
    elif stage == "external_score":
        note = store.notes(case_id)
        if not note.get("selected_entity") or not _valid_signoff(case, note, "entity"):
            store.update_workflow(
                case_id,
                status="blocked",
                blocker="企业主体尚未完成确认，不能使用外部评级或执行评分。",
                current_task="等待确认企业主体",
            )
            return case_dict(case)
        if not _valid_signoff(case, note, "financial") or not _valid_signoff(case, note, "risk"):
            store.update_workflow(
                case_id,
                status="blocked",
                blocker="财务或风险意见签署已失效，不能评分。",
                current_task="等待重新签署上游内容",
            )
            return case_dict(case)
        if not case.mock:
            store.update_workflow(
                case_id,
                status="blocked",
                blocker="当前仅配置模拟评分规则；真实案件需要获批的评分规则版本。",
                current_task="等待配置正式评分规则",
            )
            return case_dict(case)
        fx_rate = _case_fx_rate(case)
        if not fx_rate:
            store.update_workflow(
                case_id,
                status="blocked",
                blocker="缺少报表币种到 USD 的有效汇率快照。",
                current_task="等待补充汇率",
            )
            return case_dict(case)
        extracted = _signed_financial_values(case, note)
        financial = {
            value.field: value.normalized
            for value in extracted
            if value.normalized is not None
        }
        risk_draft = note.get("risk_draft") or {}
        source_book = case_source_path(case)
        method = "A. % Tangible Net Worth"
        if source_book.is_file():
            method = str(read_workbook(source_book).get("method") or method)
        result = run_macro(
            financial,
            _qualitative_choices(risk_draft),
            str((note.get("selected_entity") or {}).get("rating") or ""),
            method,
            case.requested_cl_usd,
            fx_rate,
        )
        score = {
            "rule_version": "mock-score-v1",
            "data_mode": "mock_demo",
            "input_hash": _content_hash(
                {
                    "financial_signoff": _valid_signoff(case, note, "financial")["content_hash"],
                    "risk_signoff": _valid_signoff(case, note, "risk")["content_hash"],
                    "requested_limit_usd": case.requested_cl_usd,
                    "fx_rate": fx_rate,
                    "method": method,
                }
            ),
            "financial_score": result.financial,
            "qualitative_score": result.qualitative,
            "overall_score": result.overall,
            "risk_class": result.risk_class,
            "method": method,
            "requested_limit_usd": case.requested_cl_usd,
            "suggested_limit_usd": round(result.suggested_cl, 2),
            "approval_level": result.approval,
            "fx_rate": fx_rate,
            "calculation_trace": result.breakdown,
            "notes": result.notes,
            "calculated_at": now_iso(),
        }
        _invalidate_signoffs(note, {"score", "artifact", "final"}, "评分结果已重新计算")
        note["score_result"] = score
        note.pop("current_artifact", None)
        note.pop("final_review", None)
        store.set_notes(case_id, note)
        store.update_workflow(
            case_id,
            status="running",
            current_stage="write_workbook",
            current_task="写入已签财务证据、评分和风险意见",
            blocker="",
        )
        store.add_event(
            case_id,
            "tool_completed",
            "模拟评分已完成",
            f"规则 mock-score-v1 产出建议额度 {score['suggested_limit_usd']:,.0f} USD；仅用于演示。",
            ["mock-score-v1", f"FX {fx_rate}"],
        )
    elif stage == "write_workbook":
        source = case_source_path(case)
        if source.is_file():
            note = store.notes(case_id)
            if not _valid_signoff(case, note, "financial") or not _valid_signoff(case, note, "risk"):
                store.update_workflow(
                    case_id,
                    status="blocked",
                    blocker="财务或风险签署已失效，不能生成交付文件。",
                    current_task="等待重新签署",
                )
                return case_dict(case)
            score = note.get("score_result") or {}
            if not score:
                store.update_workflow(
                    case_id,
                    status="blocked",
                    blocker="缺少可复算的评分结果。",
                    current_task="等待重新评分",
                )
                return case_dict(case)
            risk_draft = note.get("risk_draft") or {}
            company_description = str(risk_draft.get("company_description") or "").strip()
            risk_comments = str(risk_draft.get("risk_comments") or "").strip()
            if not company_description or not risk_comments:
                store.update_workflow(
                    case_id,
                    status="blocked",
                    blocker="风险意见尚未完成，不能生成对外交付文件。",
                    current_task="等待补充风险意见",
                )
                return case_dict(case)
            artifacts = list(note.get("artifacts") or [])
            version = len(artifacts) + 1
            target = EXPORTS / f"{case_id}_CRA_review_v{version}.xlsx"
            shutil.copy2(source, target)
            selected_entity = note.get("selected_entity") or {}
            extracted = _signed_financial_values(case, note)
            signed_fields = {value.field for value in extracted}
            missing_fields = sorted(set(FIELD_ROW) - signed_fields)
            if missing_fields:
                target.unlink(missing_ok=True)
                store.update_workflow(
                    case_id,
                    status="blocked",
                    blocker=(
                        "已签财务快照仍缺少字段："
                        + "、".join(missing_fields)
                        + "。请补充原始证据后重新签署。"
                    ),
                    current_task="等待补齐财务证据",
                )
                return case_dict(case)
            workbook = load_workbook(target)
            fill_ai_results(
                workbook,
                extracted,
                _validation_flags(note),
                case.new_period,
                risk_draft.get("qualitative") or {},
                company_description,
                risk_comments,
                str(selected_entity.get("rating") or ""),
                "CRA Agent signed evidence",
                dnb_duns=str(selected_entity.get("entity_id") or ""),
                data_mode="mock_demo" if case.mock else "non_mock",
            )
            workbook.save(target)
            save_macro_results(
                target,
                {
                    "financial": score["financial_score"],
                    "qualitative": score["qualitative_score"],
                    "overall": score["overall_score"],
                    "risk_class": score["risk_class"],
                    "suggested_cl": score["suggested_limit_usd"],
                    "approval": score["approval_level"],
                },
            )
            reopened = read_workbook(target)
            written = reopened.get("values", {}).get(case.new_period, {})
            expected = {
                value.field: value.normalized
                for value in extracted
                if value.normalized is not None
            }
            ai_fields = {
                str(row[0])
                for row in reopened.get("ai_rows", [])[1:]
                if row and row[0] in FIELD_ROW
            }
            mismatches = [
                field for field, value in expected.items()
                if written.get(field) != value
            ]
            if mismatches or ai_fields != set(FIELD_ROW):
                target.unlink(missing_ok=True)
                store.update_workflow(
                    case_id,
                    status="blocked",
                    blocker="Excel 重开核验失败，21 个财务字段或 AI Import 不一致。",
                    current_task="等待修复导出映射",
                )
                return case_dict(case)
            checksum = _sha256_path(target)
            artifact = {
                "artifact_id": uuid4().hex,
                "version": version,
                "type": "cra_excel",
                "path": str(target),
                "file_name": target.name,
                "checksum": checksum,
                "data_mode": "mock_demo" if case.mock else "non_mock",
                "financial_signoff_hash": _valid_signoff(case, note, "financial")["content_hash"],
                "risk_signoff_hash": _valid_signoff(case, note, "risk")["content_hash"],
                "score_input_hash": score["input_hash"],
                "created_at": now_iso(),
                "status": "current",
            }
            for previous in artifacts:
                if previous.get("status") == "current":
                    previous["status"] = "superseded"
            artifacts.append(artifact)
            _invalidate_signoffs(note, {"artifact", "final"}, "交付文件已生成新版本")
            note.update(
                {
                    "artifacts": artifacts,
                    "current_artifact": artifact,
                    "export_path": str(target),
                    "export_file_name": target.name,
                    "generated_at": artifact["created_at"],
                    "export_sections": [
                        "21 financial fields", "Review Evidence", "Risk Opinion", "Score Results"
                    ],
                    "risk_opinion_written": True,
                }
            )
            store.set_notes(case_id, note)
            store.update_workflow(
                case_id,
                status="awaiting_human",
                current_stage="final_signoff",
                current_task="等待最终额度、偏离和审批复核",
                completed_tasks=1,
                total_tasks=1,
            )
            store.open_task(
                case_id,
                "final_signoff",
                "完成最终复核",
                "核对建议额度、最终额度、偏离原因、审批等级及 artifact checksum。",
                {"score": score, "artifact": artifact},
            )
        else:
            store.update_workflow(
                case_id,
                status="blocked",
                blocker="缺少可回填的原始 CRA Excel 快照。",
            )
    return case_dict(case)


@app.post("/cases/{case_id}/financial-report/demo")
def use_demo_financial_report(
    case_id: str,
    x_cra_source_id: str = Header(default="", alias="X-CRA-Case-Source-Id"),
    x_cra_source_url: str = Header(default="", alias="X-CRA-Case-Source-Url"),
    x_cra_source_path: str = Header(default="", alias="X-CRA-Case-Source-Path"),
    x_cra_source_name: str = Header(default="", alias="X-CRA-Case-Source-Name"),
    x_cra_source_size: str = Header(default="", alias="X-CRA-Case-Source-Size"),
    x_cra_source_sha256: str = Header(default="", alias="X-CRA-Case-Source-Sha256"),
    x_cra_user: str = Header(default="", alias="X-CRA-User"),
    x_cra_name: str = Header(default="", alias="X-CRA-Name"),
    x_cra_role: str = Header(default="", alias="X-CRA-Role"),
) -> Dict[str, Any]:
    """仅用于产品演示：从交接包最近一期 SAP 数据生成一份明确标记的模拟当期抽取。"""
    principal = require_business_actor(x_cra_user, x_cra_name, x_cra_role)
    case = find_case(case_id)
    require_case_access(case, principal, "write")
    materialize_case_source(
        case,
        source_id=x_cra_source_id,
        source_url=x_cra_source_url,
        source_path=x_cra_source_path,
        source_name=x_cra_source_name,
        source_size=x_cra_source_size,
        source_sha256=x_cra_source_sha256,
    )
    workflow = store.workflow(case_id)
    if workflow["current_stage"] not in {"financial_evidence", "financial_signoff"}:
        raise HTTPException(409, "当前节点不需要财务报告")

    history = case_sap(case)
    if not history:
        demo_package = sample_path(case_id)
        if demo_package.is_file():
            history = read_package(demo_package).sap
    if not history:
        raise HTTPException(422, "当前案件没有可用于演示的历史财务数据")
    source_period = sorted(history, reverse=True)[0]
    source_values = history[source_period]
    try:
        demo_financials = build_demo_financials(source_values)
    except ValueError as exc:
        raise HTTPException(
            422, f"历史数据不足以生成可勾稽的模拟财报：{exc}"
        ) from exc
    values: List[Dict[str, Any]] = []
    for field in FIELD_ROW:
        demo_value = demo_financials.get(field)
        values.append(
            {
                "field": field,
                "period_end": case.new_period,
                "value": demo_value,
                "unit_multiplier": 1,
                "currency": case.statement_currency,
                "page": None,
                "source_text": f"[模拟情景] {field}: {demo_value}",
                "confidence": "demo",
                "note": (
                    f"基于 {source_period} 历史数据按已披露的模拟情景生成；"
                    "非客户真实财报。"
                ),
            }
        )

    store.add_event(
        case_id,
        "tool_completed",
        "已载入可勾稽的模拟财报",
        (
            f"基于 {source_period} 历史数据构造 {case.new_period} 模拟经营情景，"
            "已通过资产负债表勾稽；不得用于真实授信决策。"
        ),
        [f"历史财务 {source_period}", "模拟情景规则 v1", "资产=负债+权益"],
    )
    return queue_financial_signoff(
        case,
        values,
        source={
            "type": "demo",
            "label": "演示财报数据",
            "source_period": source_period,
            "scenario": "synthetic_operating_improvement_v1",
            "assumptions": list(DEMO_ASSUMPTIONS),
            "balance_check": "passed",
            "warning": "该数据仅用于演示 Agent 流程，不是客户真实当期财报。",
        },
    )


@app.post("/cases/{case_id}/financial-report")
async def upload_financial_report(
    case_id: str,
    file: Optional[UploadFile] = File(default=None),
    x_cra_file_id: str = Header(default="", alias="X-CRA-File-Id"),
    x_cra_file_url: str = Header(default="", alias="X-CRA-File-Url"),
    x_cra_file_path: str = Header(default="", alias="X-CRA-File-Path"),
    x_cra_file_name: str = Header(default="", alias="X-CRA-File-Name"),
    x_cra_file_size: str = Header(default="", alias="X-CRA-File-Size"),
    x_cra_file_sha256: str = Header(default="", alias="X-CRA-File-Sha256"),
    x_cra_user: str = Header(default="", alias="X-CRA-User"),
    x_cra_name: str = Header(default="", alias="X-CRA-Name"),
    x_cra_role: str = Header(default="", alias="X-CRA-Role"),
) -> Dict[str, Any]:
    """保存正式财报、抽取当前期间字段，并送入既有的人工签署节点。"""
    principal = require_business_actor(x_cra_user, x_cra_name, x_cra_role)
    case = find_case(case_id)
    require_case_access(case, principal, "write")
    workflow = store.workflow(case_id)
    if workflow["current_stage"] not in {"financial_evidence", "financial_signoff"}:
        raise HTTPException(409, "当前节点不需要财务报告")

    x_cra_file_id = _header_text(x_cra_file_id)

    report_cache, original_name, checksum = await materialize_managed_upload(
        file,
        managed_id=x_cra_file_id,
        managed_url=x_cra_file_url,
        managed_path=x_cra_file_path,
        managed_name=x_cra_file_name,
        managed_size=x_cra_file_size,
        managed_sha256=x_cra_file_sha256,
        allowed_suffixes={".pdf"},
        max_bytes=MAX_FINANCIAL_REPORT_BYTES,
        default_name="financial-report.pdf",
    )
    payload = report_cache.read_bytes()
    if not payload.startswith(b"%PDF-"):
        raise HTTPException(422, "文件内容不是有效的 PDF")

    stored_name = f"{case_id}_{uuid4().hex}.pdf"
    report_path = FINANCIAL_REPORTS / stored_name
    shutil.copy2(report_cache, report_path)
    try:
        from cra.llm import get_llm

        report = extract_from_pdf(report_path, get_llm())
    except (RuntimeError, ValueError) as exc:
        report_path.unlink(missing_ok=True)
        raise HTTPException(422, f"财报解析失败：{exc}") from exc
    except Exception as exc:
        report_path.unlink(missing_ok=True)
        logging.getLogger(__name__).warning(
            "Financial extraction failed (%s, upstream_status=%s)",
            type(exc).__name__,
            getattr(exc, "status_code", None),
        )
        raise HTTPException(502, "财报抽取服务暂时不可用，请稍后重试") from exc

    confidence_map = {"高": "high", "中": "medium", "低": "low", "推算": "medium", "—": "low"}
    values = []
    for value in report.result.values:
        if value.period_end != case.new_period:
            continue
        field_flags = [flag for flag in report.flags if flag.field == value.field]
        value_payload = value.model_dump()
        value_payload["confidence"] = confidence_map[
            check_level(value, report.pages, field_flags)
        ]
        values.append(value_payload)
    if not values:
        report_path.unlink(missing_ok=True)
        extracted_periods = sorted({value.period_end for value in report.result.values})
        period_hint = "、".join(extracted_periods) or "未识别"
        raise HTTPException(
            422,
            f"财报未抽取到案件期间 {case.new_period} 的数据（识别到：{period_hint}）",
        )

    flags = [
        {"level": flag.level, "field": flag.field, "message": flag.message}
        for flag in report.flags
    ]
    case.new_year = {
        **case.new_year,
        "pdf": original_name,
        "report_checksum": checksum,
        "scan": any(page.needs_ocr for page in report.pages),
    }
    store.upsert_case(case.__dict__, overwrite=True)
    store.add_event(
        case_id,
        "tool_completed",
        "正式财报已上传并完成抽取",
        f"已从 {original_name} 抽取 {len(values)} 个 {case.new_period} 财务字段。",
        [original_name, f"SHA-256 {checksum[:12]}…", "财报字段抽取"],
    )
    return queue_financial_signoff(
        case,
        values,
        source={
            "type": "report",
            "label": "客户正式财报",
            "filename": original_name,
            "stored_filename": stored_name,
            "source_file_id": x_cra_file_id,
            "checksum": checksum,
            "size_bytes": len(payload),
            "uploaded_at": now_iso(),
            "company": report.result.company or "",
            "statement_scope": report.result.statement_scope,
            "pages_used": report.pages_used,
            "validation_flags": flags,
        },
    )


@app.post("/cases/{case_id}/risk-opinion/regenerate")
def regenerate_risk_opinion(
    case_id: str,
    x_cra_user: str = Header(default="", alias="X-CRA-User"),
    x_cra_name: str = Header(default="", alias="X-CRA-Name"),
    x_cra_role: str = Header(default="", alias="X-CRA-Role"),
) -> Dict[str, Any]:
    """Re-run the narrative agent while preserving the previous draft as a version."""
    case = find_case(case_id)
    workflow = store.workflow(case_id)
    if workflow["current_stage"] not in {
        "risk_signoff",
        "external_score",
        "write_workbook",
        "final_signoff",
    }:
        raise HTTPException(409, "当前节点尚不能起草风险意见")
    notes = store.notes(case_id)
    _invalidate_signoffs(
        notes,
        {"risk", "score", "artifact", "final"},
        "风险意见已重新生成",
    )
    for derived in ("score_result", "current_artifact", "final_review"):
        notes.pop(derived, None)
    previous = notes.get("risk_draft") or {}
    versions = list(notes.get("risk_opinion_versions") or [])
    if previous.get("risk_comments"):
        versions.append(
            {
                "version": len(versions) + 1,
                "editor": previous.get("edited_by") or "Agent",
                "saved_at": previous.get("updated_at") or now_iso(),
                "company_description": previous.get("company_description", ""),
                "risk_comments": previous.get("risk_comments", ""),
            }
        )
    from cra.llm import get_llm

    principal = require_business_actor(x_cra_user, x_cra_name, x_cra_role)
    require_case_access(case, principal, "write")
    package = generate_risk_opinion(
        case,
        notes,
        retriever_for(principal, case=case),
        get_llm(),
    )
    notes["risk_sources"] = package["sources"]
    notes["risk_draft"] = {**package["draft"], "updated_at": now_iso()}
    notes["risk_opinion_versions"] = versions[-19:] + [
        {
            "version": len(versions) + 1,
            "editor": "Agent",
            "saved_at": notes["risk_draft"]["updated_at"],
            "company_description": notes["risk_draft"]["company_description"],
            "risk_comments": notes["risk_draft"]["risk_comments"],
        }
    ]
    store.set_notes(case_id, notes)
    if workflow["current_stage"] not in {"retrieve_context", "risk_signoff"}:
        store.open_task(
            case_id,
            "risk_signoff",
            "签署风险意见",
            "风险意见已更新，请重新复核并签署。",
        )
        store.update_workflow(
            case_id,
            status="awaiting_human",
            current_stage="risk_signoff",
            current_task="等待风险意见重新签署",
            blocker="",
        )
    store.add_event(
        case_id,
        "agent_progress",
        "风险意见已重新起草",
        f"草稿引用 {len(package['sources'])} 条案件证据或 RAG 材料，等待分析师复核。",
        [item["citation"] for item in package["sources"]],
    )
    return case_dict(case)


@app.put("/cases/{case_id}/risk-opinion")
def update_risk_opinion(
    case_id: str,
    body: RiskOpinionUpdate,
    x_cra_user: str = Header(default="", alias="X-CRA-User"),
    x_cra_name: str = Header(default="", alias="X-CRA-Name"),
    x_cra_role: str = Header(default="", alias="X-CRA-Role"),
) -> Dict[str, Any]:
    """Save an analyst-edited opinion without signing the current human gate."""
    principal = require_business_actor(x_cra_user, x_cra_name, x_cra_role)
    case = find_case(case_id)
    require_case_access(case, principal, "write")
    workflow = store.workflow(case_id)
    if workflow["status"] == "completed":
        raise HTTPException(409, "案件已归档，请启动新的复审周期进行修改")
    notes = store.notes(case_id)
    _invalidate_signoffs(
        notes,
        {"risk", "score", "artifact", "final"},
        "风险意见已编辑",
    )
    for derived in ("score_result", "current_artifact", "final_review"):
        notes.pop(derived, None)
    current = notes.get("risk_draft") or {}
    if not current:
        raise HTTPException(409, "Agent 尚未生成风险意见草稿")
    saved_at = now_iso()
    updated = {
        **current,
        "company_description": body.company_description.strip(),
        "risk_comments": body.risk_comments.strip(),
        "stance": body.stance.strip() or current.get("stance", ""),
        "qualitative": body.qualitative or current.get("qualitative", {}),
        "edited_by": principal["name"],
        "editor_role": principal["role"],
        "updated_at": saved_at,
        "draft_mode": "analyst_edited",
    }
    versions = list(notes.get("risk_opinion_versions") or [])
    versions.append(
        {
            "version": len(versions) + 1,
            "editor": principal["name"],
            "saved_at": saved_at,
            "company_description": updated["company_description"],
            "risk_comments": updated["risk_comments"],
        }
    )
    notes["risk_draft"] = updated
    notes["risk_opinion_versions"] = versions[-20:]
    store.set_notes(case_id, notes)
    if workflow["current_stage"] not in {"retrieve_context", "risk_signoff"}:
        store.open_task(
            case_id,
            "risk_signoff",
            "签署风险意见",
            "风险意见已修改，请重新复核并签署。",
        )
        store.update_workflow(
            case_id,
            status="awaiting_human",
            current_stage="risk_signoff",
            current_task="等待风险意见重新签署",
            blocker="",
        )
    store.add_event(
        case_id,
        "human_edit",
        "风险意见已保存",
        f"{principal['name']}（{principal['role']}）保存了风险意见第 {len(versions)} 版。",
    )
    return case_dict(case)


@app.post("/cases/{case_id}/tasks/{task_id}/resolve")
def resolve_human_task(
    case_id: str,
    task_id: int,
    body: ResolveTask,
    x_cra_user: str = Header(default="", alias="X-CRA-User"),
    x_cra_name: str = Header(default="", alias="X-CRA-Name"),
    x_cra_role: str = Header(default="", alias="X-CRA-Role"),
) -> Dict[str, Any]:
    principal = require_business_actor(x_cra_user, x_cra_name, x_cra_role)
    case = find_case(case_id)
    task = next(
        (item for item in store.tasks(case_id, open_only=True) if item["id"] == task_id),
        None,
    )
    if task is None:
        raise HTTPException(404, "待办不存在")
    notes = store.notes(case_id)
    require_case_access(
        case,
        principal,
        "final_signoff" if task["task_type"] == "final_signoff" else "write",
    )
    if (
        task["task_type"] == "final_signoff"
        and notes.get("owner_user_id")
        and notes["owner_user_id"] == principal["user_id"]
    ):
        raise HTTPException(409, "最终复核必须由案件负责人之外的高级信用分析师完成")
    if task["task_type"] == "final_signoff" and principal["role"] not in REVIEW_ROLES:
        raise HTTPException(403, "最终复核必须由高级信用分析师或信用经理完成")
    resolutions = notes.setdefault("human_resolutions", {})
    resolutions[task["task_type"]] = {
        **body.resolution,
        "resolved_by": principal["user_id"],
        "resolved_by_name": principal["name"],
        "resolved_by_role": principal["role"],
        "resolved_at": now_iso(),
    }
    if task["task_type"] == "entity_confirmation":
        selected_id = str(body.resolution.get("selected_entity_id", ""))
        candidates = notes.get("entity_candidates") or entity_candidates_for(case)
        selected = next(
            (item for item in candidates if item.get("entity_id") == selected_id),
            None,
        )
        if not selected:
            raise HTTPException(422, "请先选择一个有效的企业主体")
        notes["entity_candidates"] = candidates
        notes["selected_entity"] = selected
        _invalidate_signoffs(
            notes,
            {"entity", "financial", "risk", "score", "artifact", "final"},
            "企业主体选择已更新",
        )
        _record_signoff(
            case,
            notes,
            "entity",
            principal,
            "确认当前企业主体候选与本案客户相符",
        )
    elif task["task_type"] == "financial_signoff":
        values = notes.get("financial_values") or []
        if not values:
            raise HTTPException(409, "没有可签署的财务证据")
        blocking = [
            flag for flag in (notes.get("financial_source") or {}).get("validation_flags") or []
            if flag.get("level") == "error"
        ]
        if blocking:
            raise HTTPException(409, "财务证据仍有未关闭的 error，不能签署")
        _record_signoff(
            case,
            notes,
            "financial",
            principal,
            "确认财务字段、期间、单位和来源证据已核对",
        )
    elif task["task_type"] == "risk_signoff":
        if not _valid_signoff(case, notes, "financial"):
            raise HTTPException(409, "财务签署已失效，请先重新核对财务证据")
        opinion = body.resolution.get("opinion")
        if isinstance(opinion, dict):
            company_description = str(opinion.get("company_description") or "").strip()
            risk_comments = str(opinion.get("risk_comments") or "").strip()
            if not company_description or not risk_comments:
                raise HTTPException(422, "企业概况和风险意见均不能为空")
            notes["risk_draft"] = {
                **(notes.get("risk_draft") or {}),
                **opinion,
                "edited_by": principal["name"],
                "editor_role": principal["role"],
                "updated_at": now_iso(),
                "signed_at": now_iso(),
            }
        if not (notes.get("risk_draft") or {}).get("risk_comments"):
            raise HTTPException(409, "没有可签署的风险意见")
        _record_signoff(
            case,
            notes,
            "risk",
            principal,
            "确认风险意见与引用证据已复核",
        )
    elif task["task_type"] == "final_signoff":
        if not _valid_signoff(case, notes, "financial") or not _valid_signoff(case, notes, "risk"):
            raise HTTPException(409, "上游签署已失效，不能完成最终复核")
        score = notes.get("score_result") or {}
        artifact = notes.get("current_artifact") or {}
        if not score or not artifact:
            raise HTTPException(409, "评分结果或交付文件缺失")
        try:
            review = FinalReviewPayload.model_validate(body.resolution)
        except Exception as exc:
            raise HTTPException(
                422,
                "最终复核必须包含最终额度、额度方法、审批等级和文件校验码",
            ) from exc
        if not hmac.compare_digest(review.artifact_checksum, str(artifact.get("checksum") or "")):
            raise HTTPException(409, "交付文件已变化，请重新下载并复核")
        if review.method != score.get("method") or review.approval_level != score.get("approval_level"):
            raise HTTPException(409, "额度方法或审批等级与确定性评分结果不一致")
        suggested = float(score.get("suggested_limit_usd") or 0)
        deviation = round(review.final_limit_usd - suggested, 2)
        if abs(deviation) > 0.01 and not review.deviation_reason.strip():
            raise HTTPException(422, "最终额度偏离建议额度时必须填写偏离原因")
        notes["final_review"] = {
            **review.model_dump(),
            "suggested_limit_usd": suggested,
            "deviation_amount_usd": deviation,
            "deviation_pct": round(deviation / suggested, 6) if suggested else None,
            "reviewed_at": now_iso(),
        }
        _record_signoff(
            case,
            notes,
            "final",
            principal,
            "确认最终额度、偏离、审批层级与交付文件一致",
        )
    store.resolve_task(case_id, task_id)
    store.set_notes(case_id, notes)
    mapping = {
        "entity_confirmation": (
            "financial_evidence",
            "抽取并校验当期财务数据",
        ),
        "financial_signoff": (
            "retrieve_context",
            "检索当前规则、行业与历史案件",
        ),
        "risk_signoff": (
            "external_score",
            "执行确定性模拟评分与审批分级",
        ),
        "final_signoff": ("completed", "案件已完成"),
    }
    next_stage, next_task = mapping.get(
        task["task_type"], ("resolve_entity", "继续处理案件")
    )
    status = "completed" if next_stage == "completed" else "running"
    store.update_workflow(
        case_id,
        status=status,
        current_stage=next_stage,
        current_task=next_task,
        blocker="",
    )
    store.add_event(
        case_id,
        "human_resolved",
        f"{task['title']} 已完成",
        "人工责任已记录，Agent 可继续下一步。",
    )
    return case_dict(case)


@app.get("/cases/{case_id}/financial-evidence.csv")
def download_financial_evidence(
    case_id: str,
    x_cra_user: str = Header(default="", alias="X-CRA-User"),
    x_cra_name: str = Header(default="", alias="X-CRA-Name"),
    x_cra_role: str = Header(default="", alias="X-CRA-Role"),
) -> Response:
    case = find_case(case_id)
    principal = require_business_actor(x_cra_user, x_cra_name, x_cra_role)
    require_case_access(case, principal, "read")
    values = store.notes(case_id).get("financial_values", [])
    if not values:
        raise HTTPException(404, "当前案件尚无可下载的财务抽取结果")
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        ["字段代码", "字段名称", "报表期", "值", "单位倍数", "币种", "页码", "置信度", "原文", "备注"]
    )
    for value in values:
        writer.writerow(
            [
                value.get("field", ""),
                FIELD_LABELS.get(value.get("field", ""), value.get("field", "")),
                value.get("period_end", case.new_period),
                value.get("value", ""),
                value.get("unit_multiplier", 1),
                value.get("currency", case.statement_currency),
                value.get("page", ""),
                value.get("confidence", ""),
                value.get("source_text", ""),
                value.get("note", ""),
            ]
        )
    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{case_id}_financial_evidence.csv"'
        },
    )


@app.get("/cases/{case_id}/financial-report/source")
def view_financial_report_source(
    case_id: str,
    x_cra_user: str = Header(default="", alias="X-CRA-User"),
    x_cra_name: str = Header(default="", alias="X-CRA-Name"),
    x_cra_role: str = Header(default="", alias="X-CRA-Role"),
):
    """Open the exact uploaded PDF used by the evidence table in the browser."""
    case = find_case(case_id)
    principal = require_business_actor(x_cra_user, x_cra_name, x_cra_role)
    require_case_access(case, principal, "read")
    source = store.notes(case_id).get("financial_source") or {}
    stored_filename = str(source.get("stored_filename") or "")
    if not stored_filename or Path(stored_filename).name != stored_filename:
        raise HTTPException(404, "当前案件没有可查看的正式财报原文")
    report_path = FINANCIAL_REPORTS / stored_filename
    if not report_path.is_file():
        raise HTTPException(404, "财报原文文件不存在")
    return FileResponse(
        report_path,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{case_id}_financial_report.pdf"'
        },
    )


@app.get("/cases/{case_id}/source")
def download_case_source(
    case_id: str,
    x_cra_user: str = Header(default="", alias="X-CRA-User"),
    x_cra_name: str = Header(default="", alias="X-CRA-Name"),
    x_cra_role: str = Header(default="", alias="X-CRA-Role"),
):
    case = find_case(case_id)
    principal = require_business_actor(x_cra_user, x_cra_name, x_cra_role)
    require_case_access(case, principal, "read")
    path = case_source_path(case)
    if not path.is_file():
        raise HTTPException(404, "当前案件没有可下载的原始 Excel 快照")
    return FileResponse(
        path,
        filename=path.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.post("/cases/{case_id}/chat")
def case_chat(
    case_id: str,
    body: ChatRequest,
    x_cra_user: str = Header(default="", alias="X-CRA-User"),
    x_cra_name: str = Header(default="", alias="X-CRA-Name"),
    x_cra_role: str = Header(default="", alias="X-CRA-Role"),
) -> Dict[str, Any]:
    started_at = time.monotonic()
    trace_id = uuid4().hex
    case = find_case(case_id)
    principal = require_business_actor(x_cra_user, x_cra_name, x_cra_role)
    require_case_access(case, principal, "read")
    workflow = store.workflow(case_id)
    status_names = {
        "ready": "待处理",
        "running": "执行中",
        "awaiting_human": "等待人工",
        "blocked": "已阻断",
        "completed": "已完成",
    }
    stage_names = {
        "resolve_entity": "企业主体核验",
        "financial_evidence": "财务证据核验",
        "financial_signoff": "财务证据签署",
        "retrieve_context": "规则与历史检索",
        "risk_signoff": "风险意见签署",
        "external_score": "确定性评分与审批分级",
        "write_workbook": "生成 CRA Excel",
        "final_signoff": "最终复核",
    }
    notes = store.notes(case_id)
    frame = task_frame(case_id, workflow, notes)
    plan = plan_case_question(body.question, frame)
    review_date = review_date_for_question(body.question)
    demo_label = "【演示案件；不用于真实授信】\n" if case.mock else ""
    plan_view = {
        "intent": plan.intent,
        "steps": list(plan.steps),
        "review_date": review_date,
        "data_mode": "mock_demo" if case.mock else "non_mock",
        "working_memory": {
            "entity_confirmed": frame.entity_confirmed,
            "financial_evidence_present": frame.financial_evidence_present,
            "risk_draft_present": frame.risk_draft_present,
        },
    }
    def observe(outcome: str, *, source_count: int = 0, external: str = "not_requested",
                citation_ok: Optional[bool] = None) -> None:
        try:
            log_event("case_qa", {
                "trace_id": trace_id, "case_id": case_id,
                "intent": plan.intent, "steps": list(plan.steps),
                "outcome": outcome, "external_status": external,
                "source_count": source_count, "citation_ok": citation_ok,
                "latency_s": round(time.monotonic() - started_at, 3),
                "knowledge_revision": store.knowledge_revision(),
            })
        except Exception:
            logging.getLogger(__name__).warning("CRA observation event could not be recorded")
    if plan.blocker:
        observe("blocked_action" if plan.intent == "approval_or_permission_action" else "missing_authoritative_data")
        blocked_response = {
            "found": False, "answer": demo_label + plan.blocker, "sources": [],
            "plan": plan_view, "trace_id": trace_id,
        }
        if plan.intent == "live_ar_request":
            blocked_response["missing_info"] = ["授权的当前 SAP/AR 数据"]
        return blocked_response
    if plan.intent in {
        "case_status_explanation",
        "case_next_action",
        "case_evidence_inventory",
    }:
        stage = workflow["current_stage"]
        stage_label = stage_names.get(stage, stage)
        status_label = status_names.get(workflow["status"], workflow["status"])
        state_source = {
            "id": "S1",
            "citation": "本案 · 当前流程状态",
            "text": (
                f"案件 {case_id}；状态：{status_label}；当前阶段：{stage_label}；"
                f"当前任务：{workflow.get('current_task') or '未登记'}；"
                f"阻断原因：{workflow.get('blocker') or '无'}。"
            ),
        }
        confirmation = {
            "resolve_entity": "核对候选企业的名称、国家、地址和注册编号，由经办分析师确认主体。",
            "financial_evidence": "上传当期正式财报，核对主体、期间、币种、单位和报表页。",
            "financial_signoff": "逐项核对抽取值、单位、页码和原文后签署财务证据。",
            "retrieve_context": "系统正在检索已授权规则与本案证据，当前无需人工操作。",
            "risk_signoff": "核对风险立场、主要风险、建议条件和定性选项后签署。",
            "external_score": "系统按已签事实执行确定性评分，当前无需人工计算。",
            "write_workbook": "系统正在回写并核验 Excel，无需人工修改源文件。",
            "final_signoff": "高级信用分析师需核对建议额度、最终额度、偏离原因、审批层级和文件校验和。",
            "completed": "案件已经完成；如上游材料变化，应重新进入相应复核节点。",
        }.get(stage, workflow.get("current_task") or "请查看当前任务。")
        if plan.intent == "case_status_explanation":
            if workflow["status"] == "blocked":
                conclusion = (
                    f"结论：案件在{stage_label}暂停，原因是"
                    f"{workflow.get('blocker') or '关键材料尚未通过校验'}。[S1]"
                )
            elif workflow["status"] == "awaiting_human":
                conclusion = (
                    f"结论：案件没有卡死，当前处于{stage_label}，正在等待人工处理"
                    f"“{workflow.get('current_task') or confirmation}”。[S1]"
                )
            elif workflow["status"] == "completed":
                conclusion = "结论：本案已经完成最终复核并形成当前有效交付版本。[S1]"
            else:
                conclusion = (
                    f"结论：案件正在{stage_label}，当前任务是"
                    f"“{workflow.get('current_task') or '继续处理'}”。[S1]"
                )
            answer = (
                f"{demo_label}{conclusion}\n"
                f"与本案的关系：完成当前节点后，系统才会进入下一阶段。[S1]\n"
                f"下一步：{confirmation}[S1]"
            )
            sources = [state_source]
        elif plan.intent == "case_next_action":
            answer = (
                f"{demo_label}结论：当前需要处理的是{stage_label}。[S1]\n"
                f"需要确认：{confirmation}[S1]\n"
                "下一步：按页面当前任务完成核对；上游材料变化后，已有签署会自动失效并重新复核。[S1]"
            )
            sources = [state_source]
        else:
            sources = [
                {
                    "id": "S1",
                    "citation": "本案 · CRA Excel 与客户信息",
                    "text": (
                        f"客户：{case.customer.get('name') or '未登记'}；"
                        f"BP：{case.bp}；申请额度：{case.requested_cl_usd} USD。"
                    ),
                }
            ]
            categories = ["CRA Excel 与客户信息"]
            selected_entity = notes.get("selected_entity") or {}
            if selected_entity:
                sources.append(
                    {
                        "id": f"S{len(sources) + 1}",
                        "citation": "本案 · 已确认企业主体",
                        "text": (
                            f"主体名称：{selected_entity.get('name') or '未登记'}；"
                            f"主体编号：{selected_entity.get('entity_id') or '未登记'}；"
                            f"国家：{selected_entity.get('country') or '未登记'}。"
                        ),
                    }
                )
                categories.append("已确认企业主体")
            financial_source = notes.get("financial_source") or {}
            if financial_source:
                sources.append(
                    {
                        "id": f"S{len(sources) + 1}",
                        "citation": "本案 · 财务证据",
                        "text": (
                            f"来源：{financial_source.get('label') or financial_source.get('filename') or financial_source.get('type') or '未登记'}；"
                            f"已登记字段：{len(notes.get('financial_values') or [])} 项。"
                        ),
                    }
                )
                categories.append("财务证据")
            risk_sources = list(notes.get("risk_sources") or [])[:5]
            for item in risk_sources:
                sources.append(
                    {
                        "id": f"S{len(sources) + 1}",
                        "citation": str(item.get("citation") or item.get("id") or "评审参考资料"),
                        "text": str(item.get("text") or ""),
                    }
                )
            if risk_sources:
                categories.append(f"风险意见参考资料 {len(risk_sources)} 条")
            answer = (
                f"{demo_label}结论：本案当前使用了{'、'.join(categories)}。[S1]\n"
                "与本案的关系：这些资料分别支持客户识别、财务核对和风险意见，不会自动代替人工签署。[S1]\n"
                "下一步：展开“查看本次回答依据”，可逐项核对材料名称和内容。[S1]"
            )
        observe(
            "structured_case_answer",
            source_count=len(sources),
            citation_ok=True,
        )
        return {
            "found": True,
            "answer": answer,
            "sources": sources,
            "citation": {},
            "validation_attempts": 0,
            "requires_human_review": False,
            "review_task_id": None,
            "external_status": "not_requested",
            "plan": plan_view,
            "trace_id": trace_id,
        }
    lei = plan.external_lei
    external_materials: Dict[str, str] = {}
    external_status = "not_requested"
    if lei:
        if os.environ.get("CRA_ENABLE_GLEIF_MCP", "false").lower() not in {"1", "true", "yes"}:
            observe("external_disabled", external="disabled")
            return {
                "found": False,
                "answer": demo_label + "当前未启用 GLEIF 实时查询，无法核实该 LEI 的最新记录。",
                "sources": [],
                "external_status": "disabled",
                "plan": plan_view,
                "trace_id": trace_id,
            }
        try:
            record = asyncio.run(lookup_lei_via_mcp(lei))
        except Exception:
            logging.getLogger(__name__).warning("GLEIF MCP lookup failed for an explicit LEI")
            observe("external_unavailable", external="unavailable")
            return {
                "found": False,
                "answer": demo_label + "GLEIF 实时查询暂不可用；不能据此判断企业状态，请稍后重试或人工核验。",
                "sources": [],
                "external_status": "unavailable",
                "plan": plan_view,
                "trace_id": trace_id,
            }
        external_status = record["status"]
        if external_status == "not_found":
            observe("external_not_found", external="not_found")
            return {
                "found": False,
                "answer": demo_label + "GLEIF 未返回该 LEI 的记录；这不等于企业不存在，请核对号码或人工核验。",
                "sources": [{
                    "id": "S1",
                    "citation": f"外部公开资料 · GLEIF LEI {lei}",
                    "text": f"查询时点：{record['observed_at']}",
                    "url": record["source_url"],
                }],
                "external_status": "not_found",
                "plan": plan_view,
                "trace_id": trace_id,
            }
        external_materials[f"GLEIF LEI {lei}"] = (
            f"GLEIF LEI: {lei}；法定名称：{record.get('legal_name') or '未返回'}；"
            f"注册地国家：{record.get('legal_address_country') or '未返回'}；"
            f"LEI 登记状态：{record.get('registration_status') or '未返回'}；"
            f"来源更新时间：{record.get('last_update_date') or '未返回'}；"
            f"本次查询时间：{record['observed_at']}；原始记录：{record['source_url']}。"
            "此记录仅供主体核验，不自动证明与 SAP BP 匹配，不构成评级或授信意见。"
            "LAPSED 不等于企业已停止经营。"
        )
        legal_name = str(record.get("legal_name") or "未返回")
        case_name = str(case.customer.get("name") or "未登记")
        country_code = str(record.get("legal_address_country") or "")
        country = COUNTRY_ZH.get(country_code, country_code or "未返回")
        if country_code and country != country_code:
            country = f"{country}（{country_code}）"
        status_code = str(record.get("registration_status") or "")
        status = {
            "ISSUED": "有效（ISSUED）",
            "LAPSED": "已失效（LAPSED）",
            "RETIRED": "已注销（RETIRED）",
            "ANNULLED": "已撤销（ANNULLED）",
        }.get(status_code, status_code or "未返回")
        updated_at = str(record.get("last_update_date") or "未返回").split("T", 1)[0]
        def normalize_name(value: str) -> str:
            return re.sub(r"[^a-z0-9]", "", value.casefold())

        name_matches = bool(
            legal_name != "未返回"
            and normalize_name(legal_name) == normalize_name(case_name)
        )
        if name_matches:
            relationship = (
                f"与本案的关系：外部法定名称与本案客户 {case_name} 一致；"
                "但名称一致仍不足以证明与 SAP BP 完成绑定。[S1]"
            )
            next_step = (
                "下一步：继续核对注册地址和注册编号，再由经办人确认主体绑定。[S1]"
            )
        else:
            relationship = (
                f"与本案的关系：本案客户是 {case_name}，与 {legal_name} 名称不一致；"
                "不能将该 LEI 绑定到当前 SAP BP。[S1]"
            )
            next_step = (
                "下一步：请提供本案客户的官方 LEI，或按注册地址和注册编号继续人工核验。[S1]"
            )
        source_text = external_materials[f"GLEIF LEI {lei}"]
        observe(
            "external_answered",
            source_count=1,
            external="found",
            citation_ok=True,
        )
        return {
            "found": True,
            "answer": (
                "【演示案件；以下为 GLEIF 实时公开数据】\n"
                f"结论：该 LEI 对应 {legal_name}，注册国家为 {country}，"
                f"登记状态为{status}，官方记录更新于 {updated_at}。[S1]\n"
                f"{relationship}\n{next_step}"
            ),
            "sources": [
                {
                    "id": "S1",
                    "citation": f"外部公开资料 · GLEIF LEI {lei}",
                    "text": source_text,
                    "url": record["source_url"],
                }
            ],
            "citation": {},
            "validation_attempts": 0,
            "requires_human_review": False,
            "review_task_id": None,
            "external_status": external_status,
            "plan": plan_view,
            "trace_id": trace_id,
        }
    evidence_workflow = {
        **workflow,
        "status": status_names.get(workflow["status"], workflow["status"]),
        "current_stage": stage_names.get(
            workflow["current_stage"], workflow["current_stage"]
        ),
    }
    ensure_case_evidence_document(case, evidence_workflow, notes)
    retriever = retriever_for(
        principal, case=case, review_date=review_date,
        allowed_libraries=plan.libraries,
    )
    case_retriever = retriever_for(
        principal, case=case, case_id=case_id, case_only=True,
        review_date=review_date,
    )
    try:
        from cra.llm import get_llm

        result = answer_case_question(
            body.question,
            {},
            retriever,
            get_llm(),
            history=[turn.model_dump() for turn in body.history],
            case_retriever=case_retriever,
            external_materials=external_materials,
        )
        review_task_id = None
        if result.blocked:
            validation = (
                result.citation_report.__dict__ if result.citation_report else {}
            )
            review_task_id = store.open_task(
                case_id,
                "qa_review",
                "复核案件问答的来源",
                "回答经自动重检和重写后仍未通过出处或数字校验，未向分析师展示。",
                {
                    "question": body.question,
                    "validation": validation,
                    "attempts": result.validation_attempts,
                },
            )
            store.add_bad_case(
                case_id,
                node="case_chat",
                failure_type="案件问答来源校验失败",
                exposure="L1",
                impact="high",
                priority="P1",
                detection_source="逐句引用与数字出处校验",
                response="阻断回答并转人工复核",
            )
            store.add_event(
                case_id,
                "answer_blocked",
                "案件问答已阻断",
                "自动重检和重写后仍未通过来源校验，已创建人工复核任务。",
                list(result.labels.values()),
            )
        observe(
            "blocked" if result.blocked else "answered" if result.found else "no_answer",
            source_count=len(result.sources), external=external_status,
            citation_ok=result.citation_report.ok if result.citation_report else None,
        )
        return {
            "found": result.found,
            "answer": demo_label + result.answer,
            "sources": [
                {
                    "id": source_id,
                    "citation": result.labels[source_id],
                    "text": result.sources[source_id],
                }
                for source_id in result.sources
            ],
            "citation": result.citation_report.__dict__ if result.citation_report else {},
            "validation_attempts": result.validation_attempts,
            "requires_human_review": result.blocked,
            "review_task_id": review_task_id,
            "external_status": external_status,
            "plan": plan_view,
            "trace_id": trace_id,
        }
    except Exception as exc:
        hits = retriever.search(body.question, k=5)
        observe("generation_failed", source_count=len(hits), external=external_status)
        return {
            "found": False,
            "answer": demo_label + (
                "当前模型调用未完成，已返回可核对的检索来源。"
                if hits
                else "当前材料无法支持这个问题。"
            ),
            "sources": [
                {"citation": hit.chunk.citation, "text": hit.chunk.text[:500]}
                for hit in hits
            ],
            "error": type(exc).__name__,
            "plan": plan_view,
            "trace_id": trace_id,
        }


@app.get("/cases/{case_id}/export")
def export_case(
    case_id: str,
    x_cra_user: str = Header(default="", alias="X-CRA-User"),
    x_cra_name: str = Header(default="", alias="X-CRA-Name"),
    x_cra_role: str = Header(default="", alias="X-CRA-Role"),
):
    case = find_case(case_id)
    principal = require_business_actor(x_cra_user, x_cra_name, x_cra_role)
    require_case_access(case, principal, "read")
    notes = store.notes(case_id)
    artifact = notes.get("current_artifact") or {}
    path = Path(str(artifact.get("path") or ""))
    if not path.is_file():
        raise HTTPException(404, "当前案件尚未生成可下载的 Excel")
    return FileResponse(
        path,
        filename=path.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.get("/samples/northstar")
def northstar_sample():
    path = ROOT / "data" / "handoff" / "samples" / "NEW-1000201_Northstar.xlsx"
    if not path.exists():
        raise HTTPException(404, "演示 Excel 不存在")
    return FileResponse(path, filename=path.name)
