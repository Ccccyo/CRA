#!/usr/bin/env python3
"""Build an isolated CRA knowledge database from the reviewed demo manifest.

This intentionally never reads DATABASE_URL or writes to the active workbench.
All records remain non-published; the application does not attach this database.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = PROJECT_ROOT / "services" / "cra_api"
DATA_ROOT = API_ROOT / "data"
MANIFEST_PATH = DATA_ROOT / "knowledge" / "examples" / "ingest_manifest.json"
DEFAULT_DATABASE = API_ROOT / "runtime" / "knowledge-staging.db"
sys.path.insert(0, str(API_ROOT / "src"))

from cra.ingest.chunker import chunk_markdown  # noqa: E402
from cra_api.store import WorkflowStore  # noqa: E402


ALLOWED_STATUSES = {"staging", "needs_review", "needs_human_review"}
SECTION_ID = re.compile(r"^([A-Z][A-Z0-9-]+)\s*·")


def prepare_documents() -> list[tuple[dict, list[dict]]]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if manifest.get("target") != "isolated_sqlite_staging_only":
        raise ValueError("Manifest is not approved for isolated staging")
    prepared = []
    seen_sources = set()
    for item in manifest["documents"]:
        relative = Path(item["source_path"])
        source_path = (DATA_ROOT / relative).resolve()
        if DATA_ROOT.resolve() not in source_path.parents or not source_path.is_file():
            raise ValueError(f"Invalid source path: {relative}")
        if relative.as_posix() in seen_sources:
            raise ValueError(f"Duplicate source: {relative}")
        seen_sources.add(relative.as_posix())
        if item["status"] not in ALLOWED_STATUSES:
            raise ValueError(f"Published documents are not allowed: {relative}")
        content = source_path.read_text(encoding="utf-8-sig")
        if not content.strip() or not content.lstrip().startswith("# "):
            raise ValueError(f"Missing title/content: {relative}")
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        document_id = "kb-stage-" + hashlib.sha256(
            relative.as_posix().encode("utf-8")
        ).hexdigest()[:24]
        title = content.lstrip().splitlines()[0].removeprefix("# ").strip()
        metadata = {
            key: value for key, value in item.items() if key != "source_path"
        }
        metadata.update(
            {
                "managed_by": "build-knowledge-staging.py",
                "source_reference": relative.as_posix(),
                "content_hash": content_hash,
                "manifest_entry_hash": hashlib.sha256(
                    json.dumps(item, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest(),
                "manifest_version": manifest["manifest_version"],
                "is_published": False,
                "is_mock": item["source_kind"].startswith("mock_"),
                "parser": "reviewed-markdown",
            }
        )
        document = {
            "id": document_id,
            "library_id": item["library_id"],
            "title": title,
            "citation": relative.as_posix(),
            "source_content": content,
            "source_filename": source_path.name,
            "mime_type": "text/markdown",
            "metadata": metadata,
        }
        chunks = chunk_markdown(
            content, relative.as_posix(), max_chars=2400, overlap_chars=0
        )
        if not chunks:
            raise ValueError(f"No chunks: {relative}")
        rows = []
        seen_ids = set()
        for index, chunk in enumerate(chunks):
            match = SECTION_ID.search(chunk.section.rsplit(" > ", 1)[-1])
            section_id = match.group(1) if match else f"S{index:03d}"
            if section_id in seen_ids:
                raise ValueError(f"Section was split or duplicated: {relative}#{section_id}")
            seen_ids.add(section_id)
            rows.append(
                {
                    "id": f"{document_id}#{section_id}",
                    "document_id": document_id,
                    "citation": chunk.citation,
                    "section": chunk.section,
                    "page": chunk.page,
                    "text": chunk.text,
                    "metadata": {
                        **metadata,
                        "chunk_index": index,
                        "section_id": section_id,
                        "chunk_hash": hashlib.sha256(
                            chunk.text.encode("utf-8")
                        ).hexdigest(),
                    },
                }
            )
        prepared.append((document, rows))
    return prepared


def build(database: Path) -> tuple[int, int, int]:
    if database.name != "knowledge-staging.db":
        raise ValueError("Only a file named knowledge-staging.db may be written")
    if database.resolve() == (API_ROOT / "runtime" / "workbench.db").resolve():
        raise ValueError("The active workbench database must not be modified")
    prepared = prepare_documents()  # Validate all inputs before writing anything.
    database.parent.mkdir(parents=True, exist_ok=True)
    store = WorkflowStore(database)
    expected = {document["id"] for document, _ in prepared}
    for existing in store.knowledge_documents():
        if existing["id"] not in expected or (
            existing["metadata"].get("managed_by") != "build-knowledge-staging.py"
        ):
            raise ValueError("Staging database contains an unmanaged knowledge document")
    changed = 0
    for document, chunks in prepared:
        existing = store.knowledge_document(document["id"])
        if existing and (
            existing["metadata"].get("content_hash")
            == document["metadata"]["content_hash"]
            and existing["metadata"].get("manifest_version")
            == document["metadata"]["manifest_version"]
            and existing["metadata"].get("manifest_entry_hash")
            == document["metadata"]["manifest_entry_hash"]
            and existing["metadata"].get("status") == document["metadata"]["status"]
            and existing["chunk_count"] == len(chunks)
        ):
            continue
        store.replace_knowledge_document(document, chunks)
        changed += 1
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_kb_stage_doc_status
              ON knowledge_documents(library_id,
                json_extract(metadata, '$.status'),
                json_extract(metadata, '$.region'));
            CREATE INDEX IF NOT EXISTS idx_kb_stage_doc_case
              ON knowledge_documents(json_extract(metadata, '$.case_id'),
                json_extract(metadata, '$.bp'));
            CREATE INDEX IF NOT EXISTS idx_kb_stage_chunk_section
              ON knowledge_chunks(document_id, section);
            """
        )
        connection.commit()
    documents = store.knowledge_documents()
    chunks = store.knowledge_chunks()
    if len(documents) != len(prepared) or any(
        row["metadata"].get("is_published") is not False for row in documents
    ):
        raise RuntimeError("Staging verification failed")
    return changed, len(documents), len(chunks)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database", type=Path, default=DEFAULT_DATABASE,
        help="Isolated SQLite path; filename must be knowledge-staging.db",
    )
    args = parser.parse_args()
    changed, documents, chunks = build(args.database)
    print(
        json.dumps(
            {
                "database": str(args.database.resolve()),
                "changed_documents": changed,
                "documents": documents,
                "chunks": chunks,
                "published_documents": 0,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
