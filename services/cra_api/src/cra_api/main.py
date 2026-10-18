from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from cra.casefile import Case, load_cases
from cra.config import ROOT, get_settings
from cra.draft.workbench import generate_risk_opinion
from cra.excel_mock import FIELD_ROW, write_risk_opinion
from cra.extract.extractor import check_level, extract_from_pdf
from cra.extract.schema import FIELD_LABELS
from cra.handoff import case_sap, import_package, read_package, sample_path
from cra.ingest.chunker import Chunk, chunk_markdown, chunk_pages
from cra.ingest.pdf_reader import read_pages
from cra.lookup.matcher import LookupResult
from cra.qa.case_chat import answer_case_question
from cra.resources import entity_matcher, knowledge_chunks
from cra.retrieve.hybrid import build_retriever

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

RUNTIME = ROOT / "runtime"
UPLOADS = RUNTIME / "uploads"
FINANCIAL_REPORTS = UPLOADS / "financial-reports"
EXPORTS = RUNTIME / "exports"
UPLOADS.mkdir(parents=True, exist_ok=True)
FINANCIAL_REPORTS.mkdir(parents=True, exist_ok=True)
EXPORTS.mkdir(parents=True, exist_ok=True)
store = create_workflow_store(
    RUNTIME / "workbench.db", get_settings().database_url
)


class AgentAction(BaseModel):
    action: str = "continue"


class ResolveTask(BaseModel):
    resolution: Dict[str, Any] = Field(default_factory=dict)


class ChatRequest(BaseModel):
    question: str
    history: List[Dict[str, str]] = Field(default_factory=list)


class RiskOpinionUpdate(BaseModel):
    company_description: str = Field(min_length=1, max_length=2000)
    risk_comments: str = Field(min_length=1, max_length=8000)
    stance: str = Field(default="", max_length=40)
    qualitative: Dict[str, Dict[str, str]] = Field(default_factory=dict)


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


def retriever_for(principal: Dict[str, str]):
    """Build a role-scoped retriever from the current database contents."""
    allowed = ROLE_ACCESS.get(principal["role"], set())
    chunks = []
    for row in store.knowledge_chunks():
        if source_type(row["citation"]) not in allowed:
            continue
        chunks.append(
            Chunk(
                id=row["id"],
                text=row["text"],
                source=row["citation"].split("（", 1)[0],
                section=row.get("section") or "",
                page=row.get("page"),
            )
        )
    return build_retriever(
        chunks,
        use_vector=False,
        use_rerank=False,
    )


LIBRARY_PREFIX = {
    "policy": "",
    "public_reference": "公开资料/",
    "case_history": "历史申报/",
}
KNOWLEDGE_EXTENSIONS = {".md", ".txt", ".pdf"}
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
            "metadata": {"chunk_index": index},
        }
        for index, chunk in enumerate(chunks)
    ]


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
    notes = store.notes(case.case_id)
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
        "knowledge-seed-v1"
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

    if not store.has_data_migration("knowledge-seed-v1"):
        if not store.knowledge_chunks():
            rag_rows = []
            for chunk in knowledge_chunks():
                document_id = chunk.id.split("#", 1)[0]
                title = chunk.citation.split(" > ", 1)[0]
                if "（" in title:
                    title = title.split("（", 1)[1].rstrip("）")
                title = title.replace("公开资料/", "").replace("历史申报/", "")
                rag_rows.append(
                    {
                        "id": chunk.id,
                        "document_id": document_id,
                        "library_id": source_type(chunk.citation),
                        "title": title or document_id,
                        "citation": chunk.citation,
                        "section": chunk.section,
                        "page": chunk.page,
                        "text": chunk.text,
                    }
                )
            store.sync_knowledge(rag_rows)
        store.mark_data_migration("knowledge-seed-v1")
    if not store.events("CASE-001"):
        store.add_event(
            "CASE-001",
            "tool_completed",
            "CRA Excel 已导入",
            "已读取 BP、信用段、历史财务、汇率和付款表现。",
            ["Financial Statements", "Header params", "TCURR"],
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
    except Exception:
        # The database-backed API must stay available even if optional demo
        # fixture initialization encounters a temporarily unavailable Neon DB.
        logging.getLogger(__name__).exception("CRA demo seed initialization failed")


@app.on_event("startup")
def startup() -> None:
    # Seeding is idempotent but may need to wake a suspended remote database.
    # Do not make all reads wait for that maintenance work to finish.
    if os.environ.get("CRA_AUTO_SEED", "true").lower() in {"1", "true", "yes"}:
        Thread(target=seed_in_background, name="cra-demo-seed", daemon=True).start()


@app.get("/health")
def health() -> Dict[str, Any]:
    settings = get_settings()
    return {
        "status": "ok",
        "service": "cra-agent",
        "cases": len(store.cases()),
        "database_backend": store.backend,
        "database_warning": store.startup_warning,
        "llm_configured": bool(settings.deepseek_api_key),
    }


@app.get("/knowledge")
def visible_knowledge(
    x_cra_user: str = Header(default="", alias="X-CRA-User"),
    x_cra_name: str = Header(default="", alias="X-CRA-Name"),
    x_cra_role: str = Header(default="", alias="X-CRA-Role"),
) -> Dict[str, Any]:
    principal = require_request_actor(x_cra_user, x_cra_name, x_cra_role)
    stored_chunks = store.knowledge_chunks()
    chunks = [SimpleNamespace(**item) for item in stored_chunks]
    catalog = knowledge_catalog(chunks, principal)
    catalog["can_manage"] = principal["role"] in {"admin", "super_admin"}
    return catalog


@app.get("/knowledge/documents/{document_id}")
def knowledge_document_detail(
    document_id: str,
    x_cra_user: str = Header(default="", alias="X-CRA-User"),
    x_cra_name: str = Header(default="", alias="X-CRA-Name"),
    x_cra_role: str = Header(default="", alias="X-CRA-Role"),
) -> Dict[str, Any]:
    require_knowledge_admin(x_cra_user, x_cra_name, x_cra_role)
    document = store.knowledge_document(document_id)
    if document is None:
        raise HTTPException(404, "知识文档不存在")
    return knowledge_admin_payload(document)


@app.post("/knowledge/documents", status_code=201)
async def upload_knowledge_document(
    library_id: str = Form(...),
    title: str = Form(...),
    file: UploadFile = File(...),
    x_cra_user: str = Header(default="", alias="X-CRA-User"),
    x_cra_name: str = Header(default="", alias="X-CRA-Name"),
    x_cra_role: str = Header(default="", alias="X-CRA-Role"),
) -> Dict[str, Any]:
    principal = require_knowledge_admin(x_cra_user, x_cra_name, x_cra_role)
    if library_id not in LIBRARY_PREFIX:
        raise HTTPException(422, "请选择有效的知识库")
    title = title.strip()
    if not title or len(title) > 160:
        raise HTTPException(422, "文档标题需为 1至 160 个字符")
    filename = Path(file.filename or "knowledge.md").name
    suffix = Path(filename).suffix.lower()
    if suffix not in KNOWLEDGE_EXTENSIONS:
        raise HTTPException(415, "仅支持 .md、.txt 和带文字层的 .pdf 文件")
    payload = await file.read(MAX_KNOWLEDGE_UPLOAD_BYTES + 1)
    if len(payload) > MAX_KNOWLEDGE_UPLOAD_BYTES:
        raise HTTPException(413, "文档超过 10 MB 上限")
    if not payload:
        raise HTTPException(422, "上传的文档是空文件")

    pages = None
    mime_type = "application/pdf" if suffix == ".pdf" else "text/markdown"
    if suffix == ".pdf":
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temporary:
            temporary.write(payload)
            temporary_path = Path(temporary.name)
        try:
            pages = read_pages(temporary_path)
        finally:
            temporary_path.unlink(missing_ok=True)
        content = "\n\n".join(
            f"## 第 {page.number} 页\n\n{page.text.strip()}"
            for page in pages
            if page.text.strip()
        )
        if not content:
            raise HTTPException(
                422,
                "PDF 没有可读文字层，请先完成 OCR 或上传 Markdown/TXT",
            )
    else:
        try:
            content = payload.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise HTTPException(422, "文本文件请使用 UTF-8 编码") from exc
    if len(content) > 2_000_000:
        raise HTTPException(413, "解析后文本超过 200 万字符上限")

    document_id = f"rag-{uuid4().hex}"
    chunks = knowledge_rows(
        document_id,
        library_id,
        title,
        content,
        pages=pages,
    )
    document = store.replace_knowledge_document(
        {
            "id": document_id,
            "library_id": library_id,
            "title": title,
            "citation": knowledge_source(library_id, title),
            "source_content": content,
            "source_filename": filename,
            "mime_type": mime_type,
            "metadata": {
                "managed_by": "admin",
                "created_by": principal["user_id"],
                "updated_by": principal["user_id"],
            },
        },
        chunks,
    )
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
    title = body.title.strip()
    if not title:
        raise HTTPException(422, "文档标题不能只包含空格")
    content = body.content.strip()
    chunks = knowledge_rows(document_id, body.library_id, title, content)
    metadata = dict(existing.get("metadata") or {})
    metadata.update(
        {
            "managed_by": "admin",
            "updated_by": principal["user_id"],
            "edited_from_pdf": existing.get("mime_type") == "application/pdf",
        }
    )
    document = store.replace_knowledge_document(
        {
            "id": document_id,
            "library_id": body.library_id,
            "title": title,
            "citation": knowledge_source(body.library_id, title),
            "source_content": content,
            "source_filename": existing.get("source_filename") or "",
            "mime_type": "text/markdown",
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
    require_knowledge_admin(x_cra_user, x_cra_name, x_cra_role)
    if not store.delete_knowledge_document(document_id):
        raise HTTPException(404, "知识文档不存在")
    return {"deleted": True, "document_id": document_id}


@app.get("/cases")
def cases() -> List[Dict[str, Any]]:
    return store.case_summaries()


@app.get("/cases/{case_id}")
def case_detail(case_id: str) -> Dict[str, Any]:
    return case_dict(find_case(case_id))


@app.post("/cases/preview")
def preview_case(file: UploadFile = File(...)) -> Dict[str, Any]:
    path = save_upload(file)
    return package_payload(path)


@app.post("/cases")
def create_case(
    file: UploadFile = File(...),
    owner: str = Form("张俊"),
    due_date: str = Form(""),
    duplicate_mode: str = Form("review"),
) -> Dict[str, Any]:
    path = save_upload(file)
    preview = package_payload(path)
    if not preview["valid"]:
        raise HTTPException(422, detail=preview["errors"])
    if preview["duplicate"] and duplicate_mode == "review":
        raise HTTPException(
            409,
            detail={
                "message": "该 BP 已有案件，请明确选择更新或新评审周期。",
                "existing": preview["duplicate"],
            },
        )
    case, created, errors = import_package(path, force_new=duplicate_mode == "cycle")
    if errors or case is None:
        raise HTTPException(422, detail=errors)
    store.upsert_case(case.__dict__, overwrite=True)
    store.ensure_case(
        case.case_id,
        status="running",
        stage="resolve_entity",
        task="搜索并排序企业主体候选",
        completed_tasks=1,
        total_tasks=6,
    )
    store.set_notes(case.case_id, {"owner": owner, "due_date": due_date})
    store.add_event(
        case.case_id,
        "case_created" if created else "case_updated",
        "案件已创建" if created else "案件已更新",
        "已保存 Excel 快照并启动企业主体核验。",
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
) -> Dict[str, Any]:
    case = find_case(case_id)
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
        principal = require_request_actor(x_cra_user, x_cra_name, x_cra_role)
        retriever = retriever_for(principal)
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
    elif stage == "write_workbook":
        source = case_source_path(case)
        if source.is_file():
            target = EXPORTS / f"{case_id}_CRA_review.xlsx"
            shutil.copy2(source, target)
            note = store.notes(case_id)
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
                target.unlink(missing_ok=True)
                return case_dict(case)
            selected_entity = note.get("selected_entity") or {}
            write_risk_opinion(
                target,
                company_description,
                risk_comments,
                risk_draft.get("qualitative") or {},
                str(selected_entity.get("rating") or ""),
                str(selected_entity.get("entity_id") or ""),
            )
            note.update(
                {
                    "export_path": str(target),
                    "export_file_name": target.name,
                    "generated_at": now_iso(),
                    "export_sections": ["Company Description", "Risk Comments"],
                    "risk_opinion_written": True,
                }
            )
            store.set_notes(case_id, note)
            store.update_workflow(
                case_id,
                status="awaiting_human",
                current_stage="final_signoff",
                current_task="等待最终额度建议签署",
                completed_tasks=1,
                total_tasks=1,
            )
            store.open_task(
                case_id,
                "final_signoff",
                "完成最终复核",
                "Excel 版本已生成。请核对偏离原因并完成最终签署。",
            )
        else:
            store.update_workflow(
                case_id,
                status="blocked",
                blocker="缺少可回填的原始 CRA Excel 快照。",
            )
    return case_dict(case)


@app.post("/cases/{case_id}/financial-report/demo")
def use_demo_financial_report(case_id: str) -> Dict[str, Any]:
    """仅用于产品演示：从交接包最近一期 SAP 数据生成一份明确标记的模拟当期抽取。"""
    case = find_case(case_id)
    workflow = store.workflow(case_id)
    if workflow["current_stage"] not in {"financial_evidence", "financial_signoff"}:
        raise HTTPException(409, "当前节点不需要财务报告")

    history = case_sap(case)
    if not history:
        raise HTTPException(422, "交接包中没有可用的往期 SAP 财务数据")
    source_period = sorted(history, reverse=True)[0]
    source_values = history[source_period]
    values: List[Dict[str, Any]] = []
    for field in FIELD_ROW:
        prior_value = source_values.get(field)
        # 统一按 4% 演示增长保留会计等式；这不是业务预测。
        demo_value = None if prior_value is None else round(float(prior_value) * 1.04, 2)
        values.append(
            {
                "field": field,
                "period_end": case.new_period,
                "value": demo_value,
                "unit_multiplier": 1,
                "currency": case.statement_currency,
                "page": None,
                "source_text": f"[演示数据] {field}: {demo_value}",
                "confidence": "demo",
                "note": f"基于 {source_period} SAP 数值按 4% 演示增长生成，仅用于流程演示。",
            }
        )

    store.add_event(
        case_id,
        "tool_completed",
        "已载入演示财报数据",
        f"基于 {source_period} 历史数据构造 {case.new_period} 流程演示样本，不得用于真实授信决策。",
        [f"SAP {source_period}", "演示数据生成规则"],
    )
    return queue_financial_signoff(
        case,
        values,
        source={
            "type": "demo",
            "label": "演示财报数据",
            "source_period": source_period,
            "warning": "该数据仅用于演示 Agent 流程，不是客户真实当期财报。",
        },
    )


@app.post("/cases/{case_id}/financial-report")
async def upload_financial_report(
    case_id: str,
    file: UploadFile = File(...),
) -> Dict[str, Any]:
    """保存正式财报、抽取当前期间字段，并送入既有的人工签署节点。"""
    case = find_case(case_id)
    workflow = store.workflow(case_id)
    if workflow["current_stage"] not in {"financial_evidence", "financial_signoff"}:
        raise HTTPException(409, "当前节点不需要财务报告")

    original_name = Path(file.filename or "financial-report.pdf").name
    if Path(original_name).suffix.lower() != ".pdf":
        raise HTTPException(415, "仅支持 PDF 格式的正式财务报告")

    payload = await file.read(MAX_FINANCIAL_REPORT_BYTES + 1)
    if not payload:
        raise HTTPException(422, "上传的财务报告是空文件")
    if len(payload) > MAX_FINANCIAL_REPORT_BYTES:
        raise HTTPException(413, "财务报告超过 20 MB 上限")
    if not payload.startswith(b"%PDF-"):
        raise HTTPException(422, "文件内容不是有效的 PDF")

    stored_name = f"{case_id}_{uuid4().hex}.pdf"
    report_path = FINANCIAL_REPORTS / stored_name
    report_path.write_bytes(payload)
    try:
        from cra.llm import get_llm

        report = extract_from_pdf(report_path, get_llm())
    except (RuntimeError, ValueError) as exc:
        report_path.unlink(missing_ok=True)
        raise HTTPException(422, f"财报解析失败：{exc}") from exc
    except Exception as exc:
        report_path.unlink(missing_ok=True)
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
    checksum = hashlib.sha256(payload).hexdigest()
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
    if workflow["current_stage"] not in {"risk_signoff", "write_workbook", "final_signoff"}:
        raise HTTPException(409, "当前节点尚不能起草风险意见")
    notes = store.notes(case_id)
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

    principal = require_request_actor(x_cra_user, x_cra_name, x_cra_role)
    package = generate_risk_opinion(
        case,
        notes,
        retriever_for(principal),
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
    case = find_case(case_id)
    principal = require_request_actor(x_cra_user, x_cra_name, x_cra_role)
    workflow = store.workflow(case_id)
    if workflow["status"] == "completed":
        raise HTTPException(409, "案件已归档，请启动新的复审周期进行修改")
    notes = store.notes(case_id)
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
    export_path = Path(str(notes.get("export_path") or ""))
    if export_path.is_file():
        write_risk_opinion(
            export_path,
            updated["company_description"],
            updated["risk_comments"],
            updated.get("qualitative") or {},
            str((notes.get("selected_entity") or {}).get("rating") or ""),
            str((notes.get("selected_entity") or {}).get("entity_id") or ""),
        )
        notes["generated_at"] = saved_at
        notes["risk_opinion_written"] = True
    store.set_notes(case_id, notes)
    store.add_event(
        case_id,
        "human_edit",
        "风险意见已保存",
        f"{principal['name']}（{principal['role']}）保存了风险意见第 {len(versions)} 版。",
    )
    return case_dict(case)


@app.post("/cases/{case_id}/tasks/{task_id}/resolve")
def resolve_human_task(
    case_id: str, task_id: int, body: ResolveTask
) -> Dict[str, Any]:
    case = find_case(case_id)
    task = next(
        (item for item in store.tasks(case_id, open_only=True) if item["id"] == task_id),
        None,
    )
    if task is None:
        raise HTTPException(404, "待办不存在")
    notes = store.notes(case_id)
    resolutions = notes.setdefault("human_resolutions", {})
    resolutions[task["task_type"]] = {
        **body.resolution,
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
    elif task["task_type"] == "risk_signoff":
        opinion = body.resolution.get("opinion")
        if isinstance(opinion, dict):
            company_description = str(opinion.get("company_description") or "").strip()
            risk_comments = str(opinion.get("risk_comments") or "").strip()
            if not company_description or not risk_comments:
                raise HTTPException(422, "企业概况和风险意见均不能为空")
            notes["risk_draft"] = {
                **(notes.get("risk_draft") or {}),
                **opinion,
                "edited_by": "张俊",
                "editor_role": "分析师",
                "updated_at": now_iso(),
                "signed_at": now_iso(),
            }
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
            "write_workbook",
            "生成 CRA Excel 新版本",
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
def download_financial_evidence(case_id: str) -> Response:
    case = find_case(case_id)
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
def view_financial_report_source(case_id: str):
    """Open the exact uploaded PDF used by the evidence table in the browser."""
    find_case(case_id)
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
def download_case_source(case_id: str):
    case = find_case(case_id)
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
    case = find_case(case_id)
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
        "write_workbook": "生成 CRA Excel",
        "final_signoff": "最终复核",
    }
    materials = {
        "案件概况": json.dumps(case.__dict__, ensure_ascii=False),
        "Agent 状态": json.dumps(
            {
                "案件状态": status_names.get(workflow["status"], workflow["status"]),
                "当前阶段": stage_names.get(workflow["current_stage"], workflow["current_stage"]),
                "当前任务": workflow["current_task"],
                "阻断原因": workflow.get("blocker") or "无",
            },
            ensure_ascii=False,
        ),
        "案件备注": json.dumps(store.notes(case_id), ensure_ascii=False),
    }
    principal = require_request_actor(x_cra_user, x_cra_name, x_cra_role)
    retriever = retriever_for(principal)
    try:
        from cra.llm import get_llm

        result = answer_case_question(
            body.question,
            materials,
            retriever,
            get_llm(),
            history=body.history,
        )
        return {
            "found": result.found,
            "answer": result.answer,
            "sources": [
                {
                    "id": source_id,
                    "citation": result.labels[source_id],
                    "text": result.sources[source_id],
                }
                for source_id in result.sources
            ],
            "citation": result.citation_report.__dict__ if result.citation_report else {},
        }
    except Exception as exc:
        hits = retriever.search(body.question, k=5)
        return {
            "found": bool(hits),
            "answer": (
                "当前模型调用未完成，已返回可核对的检索来源。"
                if hits
                else "当前材料无法支持这个问题。"
            ),
            "sources": [
                {"citation": hit.chunk.citation, "text": hit.chunk.text[:500]}
                for hit in hits
            ],
            "error": type(exc).__name__,
        }


@app.get("/cases/{case_id}/export")
def export_case(case_id: str):
    find_case(case_id)
    path = Path(store.notes(case_id).get("export_path", ""))
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
