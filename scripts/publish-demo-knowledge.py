#!/usr/bin/env python3
"""Publish a small, explicitly labelled CRA demo corpus to the active DB.

Dry-run by default. PostgreSQL writes require an exact host/database match and
never alter pre-existing, non-script-owned knowledge. This is a demo release,
not approval of an employer's credit policy or customer evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import runpy
import sys
from pathlib import Path
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = PROJECT_ROOT / "services" / "cra_api"
sys.path.insert(0, str(API_ROOT / "src"))

from cra.config import get_settings  # noqa: E402
from cra_api.store import WorkflowStore  # noqa: E402


RELEASE_ID = "CRA-DEMO-2026-10-24"
MANAGED_BY = "publish-demo-knowledge.py"
SOURCES = {
    "knowledge/examples/demo_credit_policy_v1.md",
    "knowledge/examples/demo_credit_policy_v2.md",
    "knowledge/examples/demo_case_003_evidence.md",
    "knowledge/examples/demo_case_003_history.md",
    "knowledge/examples/gleif_identity_public_reference.md",
}


def prepare_release() -> list[tuple[dict, list[dict]]]:
    prepared = runpy.run_path(
        str(PROJECT_ROOT / "scripts" / "build-knowledge-staging.py")
    )["prepare_documents"]()
    selected = []
    for source, chunks in prepared:
        reference = source["metadata"]["source_reference"]
        if reference not in SOURCES:
            continue
        if source["metadata"].get("is_published") is not False:
            raise ValueError("Source must be staged before a demo release")
        if source["library_id"] not in {"policy", "case_evidence", "case_history", "public_reference"}:
            raise ValueError("Unexpected demo library")
        digest = hashlib.sha256(reference.encode("utf-8")).hexdigest()[:24]
        document_id = f"kb-demo-{digest}"
        label = (
            "历史申报/" if source["library_id"] == "case_history" else
            "公开资料/" if source["library_id"] == "public_reference" else ""
        ) + f"【模拟演示】{source['title']}"
        metadata = {
            **source["metadata"], "managed_by": MANAGED_BY,
            "status": "demo_published", "is_published": True,
            "demo_only": True, "demo_release_id": RELEASE_ID,
            "publication_basis": "user-authorized mock demo; not business-policy signoff",
        }
        if source["library_id"] == "case_evidence":
            metadata["evidence_review_status"] = "unverified_mock_summary"
        document = {
            **source, "id": document_id, "title": f"【模拟演示】{source['title']}",
            "citation": label, "metadata": metadata,
        }
        released_chunks = []
        for chunk in chunks:
            section_id = chunk["metadata"]["section_id"]
            citation = f"{label}（{chunk['section']}）"
            released_chunks.append({
                **chunk, "id": f"{document_id}#{section_id}",
                "document_id": document_id, "citation": citation,
                "text": (
                    "【模拟演示资料；非真实制度或客户事实；原文中的 staging "
                    "是编制时状态，当前仅在演示知识集发布。】\n" + chunk["text"]
                ),
                "metadata": {**chunk["metadata"], **metadata},
            })
        selected.append((document, released_chunks))
    if {doc["metadata"]["source_reference"] for doc, _ in selected} != SOURCES:
        raise ValueError("Demo release source allowlist is incomplete")
    proposed = runpy.run_path(
        str(PROJECT_ROOT / "scripts" / "build-rule-evidence-staging.py")
    )["prepare"]()
    if len(proposed) != 30:
        raise ValueError("Expected exactly 30 reviewed proposed rule/evidence cards")
    for source, chunks in proposed:
        source_meta = source["metadata"]
        if (
            source_meta.get("status") != "needs_human_review"
            or source_meta.get("is_published") is not False
            or source_meta.get("knowledge_class") not in {"rule", "evidence"}
            or source["library_id"] not in {"proposed_rule", "evidence_spec"}
        ):
            raise ValueError("Proposal source is not a reviewed staging card")
        reference = source_meta["source_reference"]
        document_id = "kb-demo-proposal-" + hashlib.sha256(reference.encode()).hexdigest()[:24]
        label = f"拟议风控方法/【模拟演示·未获政策签核】{source['title']}"
        metadata = {
            **source_meta,
            "managed_by": MANAGED_BY,
            "source_review_status": "needs_human_review",
            "status": "demo_published",
            "is_published": True,
            "demo_only": True,
            "demo_release_id": RELEASE_ID,
            "publication_basis": "user-authorized demo visibility; not credit-policy approval",
        }
        document = {
            **source,
            "id": document_id,
            "title": f"【拟议·演示】{source['title']}",
            "citation": label,
            "metadata": metadata,
        }
        released_chunks = [{
            **chunk,
            "id": f"{document_id}#1",
            "document_id": document_id,
            "citation": label,
            "text": "【拟议风控方法；仅限模拟案件；非正式授信政策；不得据此批准额度。】\n" + chunk["text"],
            "metadata": {**chunk["metadata"], **metadata},
        } for chunk in chunks]
        selected.append((document, released_chunks))
    return selected


def publish_local(prepared: list[tuple[dict, list[dict]]], database: Path) -> dict:
    if database.name != "workbench-demo.db":
        raise ValueError("Local demo publish requires a workbench-demo.db file")
    store = WorkflowStore(database)
    changed = 0
    for document, chunks in prepared:
        existing = store.knowledge_document(document["id"])
        if existing and (existing["metadata"].get("managed_by") != MANAGED_BY):
            raise ValueError("Refusing to overwrite unmanaged knowledge")
        if existing and existing["metadata"] == document["metadata"] and existing["chunk_count"] == len(chunks):
            continue
        store.replace_knowledge_document(document, chunks)
        changed += 1
    return {
        "mode": "local_demo_database", "database": str(database.resolve()),
        "changed_documents": changed,
        "documents": len(prepared), "chunks": sum(len(rows) for _, rows in prepared),
    }


def _assert_target(database_url: str, host: str, database: str) -> None:
    target = urlparse(database_url)
    if (
        target.scheme not in {"postgres", "postgresql"}
        or not host or not database or target.hostname != host
        or target.path.lstrip("/") != database
    ):
        raise ValueError("Configured PostgreSQL target does not match explicit host/database")


def publish_postgres(prepared: list[tuple[dict, list[dict]]], database_url: str) -> dict:
    import psycopg
    from psycopg.types.json import Jsonb

    changed = 0
    with psycopg.connect(database_url, connect_timeout=10) as connection:
        with connection.cursor() as cursor:
            ids = [document["id"] for document, _ in prepared]
            cursor.execute(
                "SELECT id, metadata FROM cra_knowledge_document WHERE id = ANY(%s) FOR UPDATE",
                (ids,),
            )
            existing = {row[0]: row[1] for row in cursor.fetchall()}
            for document, chunks in prepared:
                previous = existing.get(document["id"])
                if previous and (
                    previous.get("managed_by") != MANAGED_BY
                    or previous.get("demo_release_id") != RELEASE_ID
                ):
                    raise ValueError("Refusing to overwrite unmanaged knowledge")
                if previous == document["metadata"]:
                    cursor.execute(
                        "SELECT COUNT(*) FROM cra_knowledge_chunk WHERE document_id=%s",
                        (document["id"],),
                    )
                    if cursor.fetchone()[0] == len(chunks):
                        continue
                cursor.execute(
                    """INSERT INTO cra_knowledge_document
                       (id, library_id, title, citation, source_content,
                        source_filename, mime_type, metadata)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT(id) DO UPDATE SET
                         library_id=EXCLUDED.library_id, title=EXCLUDED.title,
                         citation=EXCLUDED.citation, source_content=EXCLUDED.source_content,
                         source_filename=EXCLUDED.source_filename,
                         mime_type=EXCLUDED.mime_type, metadata=EXCLUDED.metadata,
                         updated_at=NOW()""",
                    (
                        document["id"], document["library_id"], document["title"],
                        document["citation"], document["source_content"],
                        document["source_filename"], document["mime_type"],
                        Jsonb(document["metadata"]),
                    ),
                )
                cursor.execute(
                    "DELETE FROM cra_knowledge_chunk WHERE document_id=%s",
                    (document["id"],),
                )
                for chunk in chunks:
                    cursor.execute(
                        """INSERT INTO cra_knowledge_chunk
                           (id, document_id, citation, section, page, content, metadata)
                           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                        (
                            chunk["id"], document["id"], chunk["citation"],
                            chunk["section"], chunk["page"], chunk["text"],
                            Jsonb(chunk["metadata"]),
                        ),
                    )
                cursor.execute(
                    """INSERT INTO cra_knowledge_audit_log
                       (action, document_id, library_id, title, source_filename,
                        chunk_count, actor_user_id, actor_name, actor_role, snapshot)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        "demo_publish", document["id"], document["library_id"],
                        document["title"], document["source_filename"], len(chunks),
                        "release-script", "Mock demo release", "automation",
                        Jsonb({"demo_release_id": RELEASE_ID,
                               "content_hash": document["metadata"]["content_hash"]}),
                    ),
                )
                changed += 1
            cursor.execute(
                """SELECT COUNT(*) FROM cra_knowledge_document
                   WHERE id = ANY(%s) AND metadata->>'demo_release_id'=%s
                     AND metadata->>'is_published'='true'""",
                (ids, RELEASE_ID),
            )
            if cursor.fetchone()[0] != len(prepared):
                raise RuntimeError("Demo publication verification failed")
    return {
        "mode": "postgres_demo_release", "release_id": RELEASE_ID,
        "changed_documents": changed, "documents": len(prepared),
        "chunks": sum(len(rows) for _, rows in prepared),
    }


def withdraw_postgres(prepared: list[tuple[dict, list[dict]]], database_url: str) -> dict:
    """Recoverably hide this release from retrieval without deleting its audit trail."""
    import psycopg
    from psycopg.types.json import Jsonb

    ids = [document["id"] for document, _ in prepared]
    changed = 0
    with psycopg.connect(database_url, connect_timeout=10) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, metadata FROM cra_knowledge_document WHERE id = ANY(%s) FOR UPDATE",
                (ids,),
            )
            rows = {row[0]: row[1] for row in cursor.fetchall()}
            if len(rows) != len(ids) or any(
                meta.get("managed_by") != MANAGED_BY
                or meta.get("demo_release_id") != RELEASE_ID
                for meta in rows.values()
            ):
                raise ValueError("Demo release is incomplete or contains unmanaged data")
            for document, chunks in prepared:
                if rows[document["id"]].get("is_published") is not True:
                    continue
                withdrawal = Jsonb({"is_published": False, "status": "withdrawn"})
                cursor.execute(
                    """UPDATE cra_knowledge_document
                       SET metadata=metadata || %s, updated_at=NOW()
                       WHERE id=%s""",
                    (withdrawal, document["id"]),
                )
                cursor.execute(
                    """UPDATE cra_knowledge_chunk
                       SET metadata=metadata || %s, updated_at=NOW()
                       WHERE document_id=%s""",
                    (withdrawal, document["id"]),
                )
                if cursor.rowcount != len(chunks):
                    raise RuntimeError("Demo withdrawal chunk count mismatch")
                cursor.execute(
                    """INSERT INTO cra_knowledge_audit_log
                       (action, document_id, library_id, title, source_filename,
                        chunk_count, actor_user_id, actor_name, actor_role, snapshot)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        "demo_withdraw", document["id"], document["library_id"],
                        document["title"], document["source_filename"], len(chunks),
                        "release-script", "Mock demo release", "automation",
                        Jsonb({"demo_release_id": RELEASE_ID}),
                    ),
                )
                changed += 1
    return {"mode": "postgres_demo_withdrawal", "release_id": RELEASE_ID,
            "changed_documents": changed, "documents": len(prepared)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply-local", type=Path)
    parser.add_argument("--apply-postgres", action="store_true")
    parser.add_argument("--apply-proposals-postgres", action="store_true")
    parser.add_argument("--withdraw-postgres", action="store_true")
    parser.add_argument("--withdraw-proposals-postgres", action="store_true")
    parser.add_argument("--expected-host", default="")
    parser.add_argument("--expected-database", default="")
    args = parser.parse_args()
    if sum(bool(value) for value in (args.apply_local, args.apply_postgres, args.apply_proposals_postgres, args.withdraw_postgres, args.withdraw_proposals_postgres)) > 1:
        raise ValueError("Select exactly one apply target")
    prepared = prepare_release()
    result = {
        "mode": "dry_run", "release_id": RELEASE_ID,
        "documents": len(prepared), "chunks": sum(len(rows) for _, rows in prepared),
        "sources": [doc["metadata"]["source_reference"] for doc, _ in prepared],
    }
    if args.apply_local:
        result.update(publish_local(prepared, args.apply_local))
    elif args.apply_postgres:
        database_url = get_settings().database_url
        _assert_target(database_url, args.expected_host, args.expected_database)
        result.update(publish_postgres(prepared, database_url))
    elif args.apply_proposals_postgres:
        database_url = get_settings().database_url
        _assert_target(database_url, args.expected_host, args.expected_database)
        proposals = [item for item in prepared if item[0]["library_id"] in {"proposed_rule", "evidence_spec"}]
        result.update(publish_postgres(proposals, database_url))
    elif args.withdraw_postgres:
        database_url = get_settings().database_url
        _assert_target(database_url, args.expected_host, args.expected_database)
        result.update(withdraw_postgres(prepared, database_url))
    elif args.withdraw_proposals_postgres:
        database_url = get_settings().database_url
        _assert_target(database_url, args.expected_host, args.expected_database)
        proposals = [item for item in prepared if item[0]["library_id"] in {"proposed_rule", "evidence_spec"}]
        result.update(withdraw_postgres(proposals, database_url))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
