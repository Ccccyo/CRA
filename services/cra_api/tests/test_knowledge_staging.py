"""The isolated seed is repeatable and cannot silently publish demo facts."""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = PROJECT_ROOT / "scripts" / "build-knowledge-staging.py"
REMOTE_SYNC_SCRIPT = PROJECT_ROOT / "scripts" / "sync-knowledge-import-postgres.py"


def _build(path: Path) -> dict:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--database", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_staging_seed_is_isolated_versioned_and_idempotent(tmp_path):
    database = tmp_path / "knowledge-staging.db"
    first = _build(database)
    second = _build(database)
    assert first["changed_documents"] == 11
    assert first["documents"] == 11
    assert first["chunks"] == 51
    assert second["changed_documents"] == 0
    assert second["published_documents"] == 0

    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT citation, metadata FROM knowledge_documents"
        ).fetchall()
        chunks = connection.execute(
            "SELECT id, metadata FROM knowledge_chunks"
        ).fetchall()
        indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(knowledge_documents)")
        }
    assert all(json.loads(metadata)["is_published"] is False for _, metadata in rows)
    assert all(
        json.loads(metadata)["status"] in
        {"staging", "needs_review", "needs_human_review"}
        for _, metadata in rows
    )
    assert any(
        citation.endswith("demo_case_003_evidence.md")
        and json.loads(metadata)["access_scope"] == "case_only"
        for citation, metadata in rows
    )
    assert any(
        id_.endswith("#DEMO-EV-03")
        and json.loads(metadata)["section_id"] == "DEMO-EV-03"
        for id_, metadata in chunks
    )
    assert any(id_.endswith("#CRA-FIELD-02") for id_, _ in chunks)
    assert {
        "gleif_identity_public_reference.md",
        "dnb_rating_public_reference.md",
        "nacm_trade_credit_public_reference.md",
        "cra_mock_field_schema_reference.md",
    }.issubset({Path(citation).name for citation, _ in rows})
    assert all(
        json.loads(metadata)["source_url"].startswith("https://")
        for citation, metadata in rows
        if Path(citation).name in {
            "gleif_identity_public_reference.md",
            "dnb_rating_public_reference.md",
            "nacm_trade_credit_public_reference.md",
        }
    )
    assert "idx_kb_stage_doc_status" in indexes


def test_builder_rejects_active_database_filename(tmp_path):
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--database", str(tmp_path / "workbench.db")],
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    assert not (tmp_path / "workbench.db").exists()


def test_remote_sync_defaults_to_no_connection_and_rejects_wrong_target():
    preview = subprocess.run(
        [sys.executable, str(REMOTE_SYNC_SCRIPT)],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(preview.stdout)
    assert result["mode"] == "dry_run_no_database_connection"
    assert result["documents"] == 11
    assert result["chunks"] == 51
    assert result["published_documents"] == 0
    assert "cra_knowledge_document" not in result["target_tables"]

    rejected = subprocess.run(
        [
            sys.executable, str(REMOTE_SYNC_SCRIPT), "--apply",
            "--expected-host", "not-the-configured-database.invalid",
            "--expected-database", "neondb",
        ],
        capture_output=True,
        text=True,
    )
    assert rejected.returncode != 0
    assert "does not match explicit target" in rejected.stderr
