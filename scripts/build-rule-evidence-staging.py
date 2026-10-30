#!/usr/bin/env python3
"""Build 30 unapproved CRA rule/evidence documents in an isolated SQLite DB.

This script never reads DATABASE_URL and cannot publish to the online RAG tables.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = PROJECT_ROOT / "services" / "cra_api"
CATALOG = API_ROOT / "data" / "knowledge" / "rule_evidence_v1" / "catalog.json"
DEFAULT_DATABASE = API_ROOT / "runtime" / "rule-evidence-staging.db"
sys.path.insert(0, str(API_ROOT / "src"))

from cra.ingest.chunker import chunk_markdown  # noqa: E402
from cra_api.store import WorkflowStore  # noqa: E402


def prepare() -> list[tuple[dict, list[dict]]]:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    if catalog.get("status") != "needs_human_review" or len(catalog["documents"]) < 30:
        raise ValueError("This pack must remain an unapproved set of at least 30 documents")
    sources = {source["id"]: source for source in catalog["sources"]}
    if len(sources) != len(catalog["sources"]) or any(
        not source["url"].startswith("https://") for source in sources.values()
    ):
        raise ValueError("Sources must have unique IDs and HTTPS URLs")
    prepared = []
    seen = set()
    for card in catalog["documents"]:
        card_id = card["id"]
        if card_id in seen or card["kind"] not in {"rule", "evidence"}:
            raise ValueError(f"Duplicate or invalid document: {card_id}")
        seen.add(card_id)
        if not card["basis"] or any(source_id not in sources for source_id in card["basis"]):
            raise ValueError(f"Missing source mapping: {card_id}")
        for field in ("title", "question", "control", "evidence", "exception", "rationale", "owner"):
            if not str(card.get(field, "")).strip():
                raise ValueError(f"Missing {field}: {card_id}")
        source_lines = "\n".join(
            f"- {source_id}: {sources[source_id]['url']}；用途：{sources[source_id]['use']}"
            for source_id in card["basis"]
        )
        content = (
            f"# {card_id} · {card['title']}\n\n"
            f"> 拟议{('业务控制' if card['kind'] == 'rule' else '证据规范')}；未获公司政策负责人批准。"
            "仅供CRA演示和业务审查，不是现行授信政策、真实客户事实或法规命令。\n\n"
            f"**适用范围**：{catalog['scope']}。具体法人、地域和生效日尚待内部确认。\n\n"
            f"**业务问题**：{card['question']}\n\n"
            f"**拟议处理**：{card['control']}\n\n"
            f"**必要证据**：{card['evidence']}\n\n"
            f"**缺失、冲突与例外**：{card['exception']}\n\n"
            f"**设计理由**：{card['rationale']}\n\n"
            f"**待签核责任人**：{card['owner']}。\n\n"
            "**公开资料与适用限制**：下列资料只支持本卡所述分析方法；"
            "不提供本公司的额度阈值、审批授权或客户级事实。\n"
            f"{source_lines}\n"
        )
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        document_id = f"kb-rule-evidence-{card_id.lower()}"
        metadata = {
            "managed_by": "build-rule-evidence-staging.py",
            "pack_id": catalog["pack_id"],
            "card_id": card_id,
            "knowledge_class": card["kind"],
            "business_scope": "trade_credit",
            "source_kind": "proposed_control_from_public_references",
            "authority": "project_proposal_not_approved_policy",
            "status": "needs_human_review",
            "is_published": False,
            "is_mock": True,
            "access_scope": "isolated_staging_only",
            "region": "unconfirmed",
            "effective_from": None,
            "effective_to": None,
            "owner": card["owner"],
            "source_ids": card["basis"],
            "source_urls": [sources[source_id]["url"] for source_id in card["basis"]],
            "source_checked_at": catalog["source_checked_at"],
            "source_reference": f"knowledge/rule_evidence_v1/catalog.json#{card_id}",
            "content_hash": digest,
        }
        chunks = chunk_markdown(content, f"拟议规则证据/{card_id}", max_chars=3600, overlap_chars=0)
        if len(chunks) != 1:
            raise ValueError(f"Card must remain one complete citation unit: {card_id}")
        document = {
            "id": document_id,
            "library_id": "proposed_rule" if card["kind"] == "rule" else "evidence_spec",
            "title": f"{card_id} · {card['title']}",
            "citation": f"拟议规则证据/{card_id}",
            "source_content": content,
            "source_filename": "catalog.json",
            "mime_type": "text/markdown",
            "metadata": metadata,
        }
        rows = [{
            "id": f"{document_id}#1", "document_id": document_id,
            "citation": chunks[0].citation, "section": chunks[0].section,
            "page": None, "text": chunks[0].text,
            "metadata": {**metadata, "chunk_index": 0, "chunk_hash": hashlib.sha256(chunks[0].text.encode()).hexdigest()},
        }]
        prepared.append((document, rows))
    return prepared


def build(database: Path) -> dict:
    if database.name != "rule-evidence-staging.db":
        raise ValueError("Only rule-evidence-staging.db may be written")
    prepared = prepare()
    store = WorkflowStore(database)
    if any(item["metadata"].get("managed_by") != "build-rule-evidence-staging.py"
           for item in store.knowledge_documents()):
        raise ValueError("Target contains unmanaged documents")
    known = {document["id"] for document, _ in prepared}
    if any(item["id"] not in known for item in store.knowledge_documents()):
        raise ValueError("Target contains documents outside this pack")
    changed = 0
    for document, chunks in prepared:
        existing = store.knowledge_document(document["id"])
        if existing and existing["chunk_count"] == 1 and all(
            existing["metadata"].get(key) == value
            for key, value in document["metadata"].items()
        ):
            continue
        store.replace_knowledge_document(document, chunks)
        changed += 1
    documents = store.knowledge_documents()
    rows = store.knowledge_chunks()
    if len(documents) != len(prepared) or len(rows) != len(prepared) or any(
        item["metadata"].get("is_published") is not False for item in documents
    ):
        raise RuntimeError("Staging read-back validation failed")
    return {"database": str(database.resolve()), "changed_documents": changed,
            "documents": len(documents), "chunks": len(rows), "published_documents": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    args = parser.parse_args()
    print(json.dumps(build(args.database), ensure_ascii=False))
