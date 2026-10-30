"""Case writes automatically maintain a case-scoped, database-backed RAG view."""

import pytest
from fastapi import HTTPException

from cra.case_knowledge import (
    case_evidence_document_id, case_history_document_id, sync_all_case_evidence,
)
from cra.ingest.chunker import Chunk
from cra.retrieve.hybrid import build_retriever
from cra.retrieve.runtime import ScopedRetriever
from cra_api.store import WorkflowStore


def _case(case_id: str, name: str) -> dict:
    return {
        "case_id": case_id, "title": f"{name} 年度评审", "bp": case_id,
        "customer": {"name": name, "country": "CN"},
        "credit_segment": "0001", "requested_cl_usd": 100000,
        "statement_currency": "USD", "business": "模拟贸易赊销",
        "new_year": {}, "inputs": {}, "mock": True,
    }


def test_case_create_edit_and_notes_refresh_database_projection(tmp_path):
    store = WorkflowStore(tmp_path / "case-knowledge.db")
    store.upsert_case(_case("CASE-A", "甲公司"))
    document_id = case_evidence_document_id("CASE-A")
    first = store.knowledge_document(document_id)
    assert first["library_id"] == "case_evidence"
    assert first["metadata"]["case_id"] == "CASE-A"
    assert first["metadata"]["access_scope"] == "case_only"
    assert "甲公司" in first["source_content"]
    assert not store.sync_case_knowledge("CASE-A")

    revision = store.knowledge_revision()
    store.update_case_title("CASE-A", "甲公司 复审")
    assert "甲公司 复审" in store.knowledge_document(document_id)["source_content"]
    assert store.knowledge_revision() != revision

    store.set_notes("CASE-A", {"financial_values": [{"field": "revenue", "value": 12345}]})
    assert "12345" in store.knowledge_document(document_id)["source_content"]
    store.set_notes("CASE-A", {"financial_values": [{"field": "revenue", "value": 67890}]})
    assert "12345" not in store.knowledge_document(document_id)["source_content"]
    assert "67890" in store.knowledge_document(document_id)["source_content"]

    store.update_workflow("CASE-A", status="awaiting_human", current_stage="financial_signoff")
    assert "financial_signoff" in store.knowledge_document(document_id)["source_content"]
    assert sync_all_case_evidence(store) == {"cases": 1, "changed": 0, "failed": []}


def test_retrieval_cannot_cross_case_scope(tmp_path):
    store = WorkflowStore(tmp_path / "case-scope.db")
    store.upsert_case(_case("CASE-A", "甲公司"))
    store.upsert_case(_case("CASE-B", "乙公司"))
    chunks = [
        Chunk(
            id=row["id"], text=row["text"], source=row["citation"],
            section=row["section"],
            meta={**row["metadata"], "library_id": row["library_id"]},
        )
        for row in store.knowledge_chunks()
    ]
    retriever = ScopedRetriever(
        build_retriever(chunks, use_vector=False, use_rerank=False),
        set(), case_id="CASE-A", case_only=True,
    )
    assert retriever.search("甲公司", k=10)
    for question in ("乙公司", "公司"):
        assert all(
            hit.chunk.meta["case_id"] == "CASE-A"
            for hit in retriever.search(question, k=10)
        )


def test_reindex_endpoint_repairs_out_of_band_writer(tmp_path, monkeypatch):
    from cra_api import main

    store = WorkflowStore(tmp_path / "case-reindex.db")
    monkeypatch.setattr(main, "store", store)
    store.upsert_case(_case("CASE-A", "甲公司"))
    # Simulate the Next.js financial-signoff path that writes directly to DB.
    with store.connect() as connection:
        connection.execute(
            "INSERT INTO case_notes (case_id, payload, updated_at) VALUES (?, ?, ?)",
            ("CASE-A", '{"financial_values":[{"value":54321}]}', "2026-10-26"),
        )
        connection.commit()
    result = main.reindex_case_evidence(
        "CASE-A", x_cra_user="reviewer", x_cra_name="Reviewer",
        x_cra_role="credit_analyst",
    )
    assert result["changed"] is True
    assert "54321" in store.knowledge_document(result["document_id"])["source_content"]
    repeated = main.reindex_case_evidence(
        "CASE-A", x_cra_user="reviewer", x_cra_name="Reviewer",
        x_cra_role="credit_analyst",
    )
    assert repeated["changed"] is False


def test_index_failure_does_not_undo_committed_case_write(tmp_path, monkeypatch):
    from cra import case_knowledge

    store = WorkflowStore(tmp_path / "case-index-failure.db")
    store.upsert_case(_case("CASE-A", "甲公司"))
    original = case_knowledge.sync_case_evidence

    def fail_sync(*args):
        raise RuntimeError("simulated index outage")

    monkeypatch.setattr(case_knowledge, "sync_case_evidence", fail_sync)
    store.update_case_title("CASE-A", "甲公司 已改名")
    assert store.case("CASE-A")["title"] == "甲公司 已改名"
    incidents = [row for row in store.bad_cases("CASE-A") if row["node"] == "case_knowledge_sync"]
    assert len(incidents) == 1 and incidents[0]["status"] == "open"

    monkeypatch.setattr(case_knowledge, "sync_case_evidence", original)
    assert store.sync_case_knowledge("CASE-A")
    assert "甲公司 已改名" in store.knowledge_document(case_evidence_document_id("CASE-A"))["source_content"]
    incidents = [row for row in store.bad_cases("CASE-A") if row["node"] == "case_knowledge_sync"]
    assert incidents[0]["status"] == "resolved"


def test_only_completed_mock_cases_enter_demo_history_and_withdraw_on_reopen(tmp_path, monkeypatch):
    from cra_api.access import knowledge_catalog

    release = "CRA-DEMO-2026-10-24"
    monkeypatch.setenv("CRA_DEMO_KB_RELEASE_ID", release)
    store = WorkflowStore(tmp_path / "history-knowledge.db")
    store.upsert_case(_case("CASE-A", "甲公司"))
    store.upsert_case(_case("CASE-B", "乙公司"))
    archive_id = case_history_document_id("CASE-A")
    assert store.knowledge_document(archive_id) is None
    store.update_workflow("CASE-A", status="completed", current_stage="completed")

    archive = store.knowledge_document(archive_id)
    assert archive["library_id"] == "case_history"
    assert archive["metadata"]["demo_release_id"] == release
    assert archive["metadata"]["bp"] == "CASE-A"
    assert archive["metadata"]["is_published"] is True
    assert "流程完成不等于授信获批" in archive["source_content"]
    assert store.knowledge_document(case_history_document_id("CASE-B")) is None

    principal = {"user_id": "demo", "name": "Demo", "role": "credit_analyst"}
    catalog = knowledge_catalog(store.knowledge_documents(), principal, release)
    history = next(row for row in catalog["libraries"] if row["id"] == "case_history")
    assert history["document_count"] == 1
    assert history["documents"][0]["read_only"] is True
    assert knowledge_catalog(store.knowledge_documents(), principal, "")["libraries"][2]["document_count"] == 0

    from cra_api import main
    monkeypatch.setattr(main, "store", store)
    detail = main.knowledge_document_detail(
        archive_id, x_cra_user="demo", x_cra_name="Demo", x_cra_role="credit_analyst"
    )
    assert "流程完成不等于授信获批" in detail["source_content"]
    with pytest.raises(HTTPException) as edit_error:
        main.update_knowledge_document(
            archive_id,
            main.KnowledgeDocumentUpdate(
                title="不可覆盖", library_id="case_history", content="修改"
            ),
            x_cra_user="admin-test", x_cra_name="Admin", x_cra_role="admin",
        )
    assert edit_error.value.status_code == 403

    chunks = [
        Chunk(
            id=row["id"], text=row["text"], source=row["citation"],
            section=row["section"], meta={**row["metadata"], "library_id": row["library_id"]},
        ) for row in store.knowledge_chunks()
    ]
    shared = build_retriever(chunks, use_vector=False, use_rerank=False)
    own = ScopedRetriever(shared, {"case_history"}, bp="CASE-A", demo_case=True,
                          demo_release_id=release)
    other = ScopedRetriever(shared, {"case_history"}, bp="CASE-B", demo_case=True,
                            demo_release_id=release)
    no_release = ScopedRetriever(shared, {"case_history"}, bp="CASE-A", demo_case=True)
    assert any(hit.chunk.meta.get("source_kind") == "completed_case_archive"
               for hit in own.search("甲公司 年度评审", k=10))
    assert not any(hit.chunk.meta.get("source_kind") == "completed_case_archive"
                   for hit in other.search("甲公司 年度评审", k=10))
    assert not any(hit.chunk.meta.get("source_kind") == "completed_case_archive"
                   for hit in no_release.search("甲公司 年度评审", k=10))

    store.update_workflow("CASE-A", status="running", current_stage="resolve_entity")
    withdrawn = store.knowledge_document(archive_id)
    assert withdrawn["metadata"]["is_published"] is False
    assert withdrawn["chunk_count"] == 0
    assert knowledge_catalog(store.knowledge_documents(), principal, release)["libraries"][2]["document_count"] == 0
