#!/usr/bin/env python3
"""Backfill/reconcile private case RAG documents from the authoritative case DB.

Dry-run by default. PostgreSQL writes require an explicit host/database match.
No raw uploaded PDF or Excel bytes are copied into the knowledge tables.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "cra_api" / "src"))

from cra.case_knowledge import (  # noqa: E402
    case_evidence_document_id, case_history_document_id, sync_all_case_evidence,
)
from cra.config import get_settings  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write idempotent case projections")
    parser.add_argument("--expected-host", default="")
    parser.add_argument("--expected-database", default="")
    parser.add_argument("--sqlite-path", type=Path, help="Explicit local SQLite workbench path")
    args = parser.parse_args()
    database_url = get_settings().database_url
    target = urlparse(database_url)
    mode = "sqlite" if args.sqlite_path else "postgresql"
    if not args.apply:
        print(json.dumps({"mode": "dry_run_no_database_connection", "backend": mode,
                          "target_host": target.hostname if mode == "postgresql" else None,
                          "target_database": target.path.lstrip("/") if mode == "postgresql" else None,
                          "sqlite_path": str(args.sqlite_path) if args.sqlite_path else None},
                         ensure_ascii=False))
        return
    if args.sqlite_path:
        if args.sqlite_path.name not in {"workbench.db", "workbench-demo.db"}:
            parser.error("SQLite target must be a named CRA workbench database")
        if not args.sqlite_path.is_file():
            parser.error("SQLite target must already exist")
        from cra_api.store import WorkflowStore

        store = WorkflowStore(args.sqlite_path)
    else:
        if (
            target.scheme not in {"postgres", "postgresql"}
            or not args.expected_host or not args.expected_database
            or target.hostname != args.expected_host
            or target.path.lstrip("/") != args.expected_database
        ):
            parser.error("Configured PostgreSQL target does not match explicit host/database")
        from cra_api.store_postgres import PostgresWorkflowStore

        store = PostgresWorkflowStore(database_url)
    result = sync_all_case_evidence(store)
    if result["failed"]:
        raise RuntimeError(f"Case index reconciliation failed for {len(result['failed'])} cases")
    verified = sum(
        1 for case in store.cases()
        if (
            (document := store.knowledge_document(case_evidence_document_id(case["case_id"])))
            and document["library_id"] == "case_evidence"
            and document["metadata"].get("case_id") == case["case_id"]
            and document["metadata"].get("access_scope") == "case_only"
            and document["chunk_count"] > 0
        )
    )
    if verified != result["cases"]:
        raise RuntimeError(f"Case index verification failed: {verified}/{result['cases']}")
    release_id = os.environ.get("CRA_DEMO_KB_RELEASE_ID", "").strip()
    completed_mock = [
        case for case in store.cases()
        if release_id and case.get("mock") is True
        and store.workflow(case["case_id"])["status"] == "completed"
    ]
    verified_history = sum(
        1 for case in completed_mock
        if (
            (document := store.knowledge_document(case_history_document_id(case["case_id"])))
            and document["library_id"] == "case_history"
            and document["metadata"].get("case_id") == case["case_id"]
            and document["metadata"].get("bp") == case["bp"]
            and document["metadata"].get("demo_release_id") == release_id
            and document["metadata"].get("is_published") is True
            and document["chunk_count"] > 0
        )
    )
    if verified_history != len(completed_mock):
        raise RuntimeError(
            f"Completed-case history verification failed: {verified_history}/{len(completed_mock)}"
        )
    print(json.dumps({"mode": f"{mode}_applied", **result,
                      "verified": verified, "completed_history_verified": verified_history,
                      "knowledge_revision": store.knowledge_revision()},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
