#!/usr/bin/env python3
"""Sync the reviewed CRA knowledge manifest into isolated PostgreSQL import tables.

The online CRA service reads cra_knowledge_document / cra_knowledge_chunk, not
these import tables. This script never publishes documents or touches online
knowledge, cases, users, or audit tables.
"""

from __future__ import annotations

import argparse
import json
import runpy
import sys
from pathlib import Path
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = PROJECT_ROOT / "services" / "cra_api"
sys.path.insert(0, str(API_ROOT / "src"))

from cra.config import get_settings  # noqa: E402


BUILDER = Path(__file__).with_name("build-knowledge-staging.py")
DOCUMENT_TABLE = "cra_knowledge_import_document"
CHUNK_TABLE = "cra_knowledge_import_chunk"


def prepare() -> list[tuple[dict, list[dict]]]:
    # Reuse the staging builder's source validation, stable ids and chunking.
    prepared = runpy.run_path(str(BUILDER))["prepare_documents"]()
    for document, chunks in prepared:
        metadata = document["metadata"]
        if metadata.get("is_published") is not False:
            raise ValueError("Import contains a published document")
        if metadata.get("managed_by") != "build-knowledge-staging.py":
            raise ValueError("Import contains an unmanaged document")
        if not chunks or any(
            chunk["metadata"].get("is_published") is not False
            for chunk in chunks
        ):
            raise ValueError("Import contains an invalid chunk")
    return prepared


def sync(prepared: list[tuple[dict, list[dict]]], database_url: str) -> dict:
    import psycopg
    from psycopg.types.json import Jsonb

    with psycopg.connect(database_url, connect_timeout=10) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {DOCUMENT_TABLE} (
                  id TEXT PRIMARY KEY,
                  library_id TEXT NOT NULL,
                  title TEXT NOT NULL,
                  citation TEXT NOT NULL,
                  source_content TEXT NOT NULL,
                  source_filename TEXT NOT NULL,
                  mime_type TEXT NOT NULL,
                  metadata JSONB NOT NULL,
                  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {CHUNK_TABLE} (
                  id TEXT PRIMARY KEY,
                  document_id TEXT NOT NULL REFERENCES {DOCUMENT_TABLE}(id)
                    ON DELETE CASCADE,
                  citation TEXT NOT NULL,
                  section TEXT NOT NULL,
                  page INTEGER,
                  content TEXT NOT NULL,
                  metadata JSONB NOT NULL,
                  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute(
                f"CREATE INDEX IF NOT EXISTS idx_cra_knowledge_import_chunk_document "
                f"ON {CHUNK_TABLE}(document_id)"
            )
            cursor.execute(
                f"SELECT id, metadata FROM {DOCUMENT_TABLE}"
            )
            existing = {row[0]: row[1] for row in cursor.fetchall()}
            expected_ids = {document["id"] for document, _ in prepared}
            if any(
                doc_id not in expected_ids
                or metadata.get("managed_by") != "build-knowledge-staging.py"
                for doc_id, metadata in existing.items()
            ):
                raise ValueError("Import tables contain unmanaged or unexpected documents")

            changed = 0
            for document, chunks in prepared:
                metadata = document["metadata"]
                prior = existing.get(document["id"])
                if prior and (
                    prior.get("content_hash") == metadata["content_hash"]
                    and prior.get("manifest_entry_hash") == metadata["manifest_entry_hash"]
                    and prior.get("manifest_version") == metadata["manifest_version"]
                ):
                    cursor.execute(
                        f"SELECT COUNT(*) FROM {CHUNK_TABLE} WHERE document_id = %s",
                        (document["id"],),
                    )
                    if cursor.fetchone()[0] == len(chunks):
                        continue
                cursor.execute(
                    f"""
                    INSERT INTO {DOCUMENT_TABLE}
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
                        document["id"], document["library_id"], document["title"],
                        document["citation"], document["source_content"],
                        document["source_filename"], document["mime_type"],
                        Jsonb(metadata),
                    ),
                )
                cursor.execute(
                    f"DELETE FROM {CHUNK_TABLE} WHERE document_id = %s",
                    (document["id"],),
                )
                for chunk in chunks:
                    cursor.execute(
                        f"""
                        INSERT INTO {CHUNK_TABLE}
                        (id, document_id, citation, section, page, content, metadata)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            chunk["id"], document["id"], chunk["citation"],
                            chunk["section"], chunk["page"], chunk["text"],
                            Jsonb(chunk["metadata"]),
                        ),
                    )
                changed += 1
            cursor.execute(
                f"SELECT COUNT(*), COUNT(*) FILTER "
                f"(WHERE metadata->>'is_published' = 'true') FROM {DOCUMENT_TABLE}"
            )
            document_count, published_count = cursor.fetchone()
            cursor.execute(f"SELECT COUNT(*) FROM {CHUNK_TABLE}")
            chunk_count = cursor.fetchone()[0]
            if (
                document_count != len(prepared)
                or chunk_count != sum(len(chunks) for _, chunks in prepared)
                or published_count != 0
            ):
                raise RuntimeError("Import table verification failed; transaction rolled back")
    return {
        "changed_documents": changed,
        "documents": document_count,
        "chunks": chunk_count,
        "published_documents": published_count,
        "target_tables": [DOCUMENT_TABLE, CHUNK_TABLE],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write to isolated import tables")
    parser.add_argument("--expected-host", default="", help="Required with --apply")
    parser.add_argument("--expected-database", default="", help="Required with --apply")
    args = parser.parse_args()
    prepared = prepare()
    result = {
        "documents": len(prepared),
        "chunks": sum(len(chunks) for _, chunks in prepared),
        "published_documents": 0,
        "target_tables": [DOCUMENT_TABLE, CHUNK_TABLE],
    }
    if not args.apply:
        result["mode"] = "dry_run_no_database_connection"
        print(json.dumps(result, ensure_ascii=False))
        return
    database_url = get_settings().database_url
    target = urlparse(database_url)
    if (
        target.scheme not in {"postgres", "postgresql"}
        or not args.expected_host
        or not args.expected_database
        or target.hostname != args.expected_host
        or target.path.lstrip("/") != args.expected_database
    ):
        raise ValueError("Configured database does not match explicit target")
    result.update(sync(prepared, database_url))
    result["mode"] = "isolated_import_tables"
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
