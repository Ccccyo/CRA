from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, Iterable, List
from urllib.parse import unquote

from cra.config import ROOT
from cra.resources import knowledge_source_type


ROLE_ACCESS = {
    "super_admin": {"policy", "public_reference", "case_history"},
    "admin": {"policy", "public_reference", "case_history"},
    "分析师": {"policy", "public_reference"},
    "高级分析师": {"policy", "public_reference", "case_history"},
    "区域信用经理": {"policy", "public_reference", "case_history"},
    "credit_analyst": {"policy", "public_reference"},
    "senior_credit_analyst": {"policy", "public_reference", "case_history"},
    "credit_manager": {"policy", "public_reference", "case_history"},
    "quality_manager": {"policy", "public_reference", "case_history"},
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
        "required_role": "高级分析师及以上",
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


def knowledge_catalog(chunks: Iterable[Any], principal: Dict[str, str]) -> Dict[str, Any]:
    access = ROLE_ACCESS.get(principal["role"], set())
    documents: Dict[str, Dict[str, Dict[str, Any]]] = {
        item["id"]: {} for item in LIBRARIES
    }
    chunk_totals = {item["id"]: 0 for item in LIBRARIES}

    for chunk in chunks:
        library_id = source_type(chunk.citation)
        chunk_totals[library_id] += 1
        document_id = chunk.id.split("#", 1)[0]
        document = documents[library_id].setdefault(
            document_id,
            {
                "id": document_id,
                "title": _document_title(chunk.citation, document_id),
                "citation": chunk.citation,
                "preview": chunk.text[:220].strip(),
                "chunk_count": 0,
                "chunks": [],
            },
        )
        document["chunk_count"] += 1
        document["chunks"].append(
            {
                "id": chunk.id,
                "citation": chunk.citation,
                "section": chunk.section,
                "page": chunk.page,
                "text": chunk.text,
            }
        )

    libraries: List[Dict[str, Any]] = []
    for definition in LIBRARIES:
        library_id = definition["id"]
        accessible = library_id in access
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
