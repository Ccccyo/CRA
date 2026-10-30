from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, Iterable, List
from urllib.parse import unquote

from cra.config import ROOT
from cra.resources import knowledge_source_type


ROLE_ACCESS = {
    "super_admin": {"policy", "public_reference", "case_history", "proposed_rule", "evidence_spec"},
    "admin": {"policy", "public_reference", "case_history", "proposed_rule", "evidence_spec"},
    "分析师": {"policy", "public_reference", "case_history", "proposed_rule", "evidence_spec"},
    "高级分析师": {"policy", "public_reference", "case_history", "proposed_rule", "evidence_spec"},
    "区域信用经理": {"policy", "public_reference", "case_history", "proposed_rule", "evidence_spec"},
    "credit_analyst": {"policy", "public_reference", "case_history", "proposed_rule", "evidence_spec"},
    "senior_credit_analyst": {"policy", "public_reference", "case_history", "proposed_rule", "evidence_spec"},
    "credit_manager": {"policy", "public_reference", "case_history", "proposed_rule", "evidence_spec"},
    "quality_manager": {"policy", "public_reference", "case_history", "proposed_rule", "evidence_spec"},
}

LIBRARIES = [
    {
        "id": "policy",
        "label": "公司制度",
        "description": "授信政策、评分方法、字段口径与操作规范。",
        "required_role": "信用分析师及以上",
    },
    {
        "id": "public_reference",
        "label": "公开资料",
        "description": "国家风险、评级释义与行业公开参考。",
        "required_role": "信用分析师及以上",
    },
    {
        "id": "case_history",
        "label": "历史案件",
        "description": "既往申报、审批判断与处置经验。",
        "required_role": "信用分析师及以上",
    },
    {
        "id": "proposed_rule",
        "label": "拟议风控规则（演示）",
        "description": "公开资料启发的待签核控制建议；不是现行授信制度。",
        "required_role": "演示知识版本下的信用分析师及以上",
        "read_only": True,
    },
    {
        "id": "evidence_spec",
        "label": "证据规范（演示）",
        "description": "需要哪些证据、如何引用；不包含实际客户资料。",
        "required_role": "演示知识版本下的信用分析师及以上",
        "read_only": True,
    },
]


def actor(user_id: str) -> Dict[str, str] | None:
    path = ROOT / "data" / "auth" / "demo_users.csv"
    if not path.is_file():
        return None
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("uid", "").strip().upper() == user_id.strip().upper():
                return {
                    "user_id": row["uid"].strip().upper(),
                    "name": row["name"].strip(),
                    "role": row["role"].strip(),
                }
    return None


def request_actor(user_id: str, name: str = "", role: str = "") -> Dict[str, str] | None:
    """Resolve legacy demo users or a user authenticated by the Next.js proxy."""
    principal = actor(user_id)
    if principal is not None:
        return principal
    name = name if isinstance(name, str) else ""
    role = role if isinstance(role, str) else ""
    if not user_id.strip() or not name.strip() or role not in ROLE_ACCESS:
        return None
    return {
        "user_id": user_id.strip(),
        "name": unquote(name).strip(),
        "role": role,
    }


def source_type(citation: str) -> str:
    return knowledge_source_type(citation)


def _document_title(citation: str, fallback: str) -> str:
    value = citation.split(" > ", 1)[0]
    if "（" in value:
        value = value.split("（", 1)[1].rstrip("）")
    return value.replace("公开资料/", "").replace("历史申报/", "") or fallback


def knowledge_catalog(
    source_documents: Iterable[Dict[str, Any]], principal: Dict[str, str],
    demo_release_id: str = "",
) -> Dict[str, Any]:
    access = ROLE_ACCESS.get(principal["role"], set())
    documents: Dict[str, Dict[str, Dict[str, Any]]] = {
        item["id"]: {} for item in LIBRARIES
    }
    chunk_totals = {item["id"]: 0 for item in LIBRARIES}

    for source_document in source_documents:
        metadata = source_document.get("metadata") or {}
        is_case_archive = metadata.get("source_kind") == "completed_case_archive"
        if is_case_archive and (
            source_document.get("library_id") != "case_history"
            or not demo_release_id
            or metadata.get("demo_release_id") != demo_release_id
            or metadata.get("demo_only") is not True
            or metadata.get("is_mock") is not True
            or metadata.get("is_published") is not True
            or metadata.get("workflow_status") != "completed"
            or metadata.get("authority") != "derived_completed_case_not_approval"
        ):
            continue
        if source_document.get("library_id") in {"proposed_rule", "evidence_spec"} and (
            not demo_release_id
            or metadata.get("demo_release_id") != demo_release_id
            or metadata.get("demo_only") is not True
            or metadata.get("is_published") is not True
        ):
            continue
        if demo_release_id and principal["role"] not in {"admin", "super_admin"}:
            if (
                metadata.get("demo_release_id") != demo_release_id
                or metadata.get("is_published") is not True
                or (source_document.get("library_id") == "case_history" and not is_case_archive)
            ):
                continue
        library_id = source_document.get("library_id") or source_type(
            source_document.get("citation", "")
        )
        if library_id not in documents:
            continue
        chunk_count = int(source_document.get("chunk_count") or 0)
        chunk_totals[library_id] += chunk_count
        document_id = source_document["id"]
        documents[library_id][document_id] = {
            "id": document_id,
            "title": source_document.get("title")
            or _document_title(source_document.get("citation", ""), document_id),
            "citation": source_document.get("citation", ""),
            "source_filename": source_document.get("source_filename") or "",
            "mime_type": source_document.get("mime_type") or "text/markdown",
            "chunk_count": chunk_count,
            "updated_at": source_document.get("updated_at") or "",
            "parser": metadata.get("parser", "legacy"),
            "has_original": bool(metadata.get("source_storage_name")),
            "demo_only": metadata.get("demo_only") is True,
            "document_version": metadata.get("document_version") or "",
            "effective_from": metadata.get("effective_from") or "",
            "effective_to": metadata.get("effective_to") or "",
            "read_only": metadata.get("managed_by") == "case_knowledge.py",
        }

    libraries: List[Dict[str, Any]] = []
    for definition in LIBRARIES:
        library_id = definition["id"]
        accessible = library_id in access and (
            library_id not in {"proposed_rule", "evidence_spec"}
            or bool(demo_release_id)
            or principal["role"] in {"admin", "super_admin"}
        )
        visible_documents = list(documents[library_id].values()) if accessible else []
        libraries.append(
            {
                **definition,
                "accessible": accessible,
                "document_count": len(documents[library_id]),
                "chunk_count": chunk_totals[library_id],
                "documents": visible_documents,
            }
        )

    return {
        "principal": principal,
        "permission_model": "role_based",
        "granted_library_count": sum(1 for item in libraries if item["accessible"]),
        "libraries": libraries,
    }
