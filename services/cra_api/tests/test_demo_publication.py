"""Demo release must remain labelled, scoped, and repeatable."""

import runpy
from pathlib import Path

from cra.ingest.chunker import Chunk
from cra.retrieve.hybrid import build_retriever
from cra.retrieve.runtime import ScopedRetriever
from cra_api.access import knowledge_catalog
from cra_api.store import WorkflowStore


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = PROJECT_ROOT / "scripts" / "publish-demo-knowledge.py"


def _release_module():
    return runpy.run_path(str(SCRIPT))


def test_demo_release_is_allowlisted_labelled_and_local_idempotent(tmp_path):
    module = _release_module()
    prepared = module["prepare_release"]()
    assert len(prepared) == 35
    assert sum(len(chunks) for _, chunks in prepared) == 54
    assert all(doc["title"].startswith(("【模拟演示】", "【拟议·演示】")) for doc, _ in prepared)
    assert all(doc["metadata"]["demo_only"] is True for doc, _ in prepared)
    assert all(doc["metadata"]["is_published"] is True for doc, _ in prepared)
    assert all(
        "非真实制度或客户事实" in chunk["text"] or "非正式授信政策" in chunk["text"]
        for _, chunks in prepared for chunk in chunks
    )
    path = tmp_path / "workbench-demo.db"
    first = module["publish_local"](prepared, path)
    second = module["publish_local"](prepared, path)
    assert first["changed_documents"] == 35
    assert second["changed_documents"] == 0
    assert len(WorkflowStore(path).knowledge_chunks()) == 54


def test_demo_policy_date_case_and_bp_are_enforced_before_retrieval():
    module = _release_module()
    prepared = module["prepare_release"]()
    chunks = [
        Chunk(
            id=chunk["id"], text=chunk["text"], source=chunk["citation"],
            section=chunk["section"], meta={
                **chunk["metadata"], "library_id": document["library_id"],
            },
        )
        for document, rows in prepared for chunk in rows
    ]
    shared = build_retriever(chunks, use_vector=False, use_rerank=False)
    kwargs = {
        "credit_segment": "0001", "region": "MX", "bp": "1000601",
        "demo_release_id": module["RELEASE_ID"], "demo_case": True,
    }
    old = ScopedRetriever(shared, {"policy"}, review_date="2026-09-30", **kwargs)
    current = ScopedRetriever(shared, {"policy"}, review_date="2026-10-24", **kwargs)
    assert old.search("财报有效性", k=20)
    assert all("demo_credit_policy_v2" not in hit.chunk.id for hit in old.search("财报有效性", k=20))
    assert current.search("财报有效性", k=20)
    assert all("V1" not in hit.chunk.section for hit in current.search("财报有效性", k=20))

    wrong_segment = ScopedRetriever(
        shared, {"policy"}, review_date="2026-10-24",
        **{**kwargs, "credit_segment": "9999"},
    )
    assert wrong_segment.search("财报有效性", k=20) == []

    own_history = ScopedRetriever(
        shared, {"case_history"}, review_date="2026-10-24", **kwargs,
    )
    other_history = ScopedRetriever(
        shared, {"case_history"}, review_date="2026-10-24",
        **{**kwargs, "bp": "other"},
    )
    assert own_history.search("440000 历史审批", k=20)
    assert other_history.search("440000 历史审批", k=20) == []

    own_case = ScopedRetriever(
        shared, set(), case_id="CASE-003", case_only=True, **kwargs,
    )
    other_case = ScopedRetriever(
        shared, set(), case_id="CASE-001", case_only=True, **kwargs,
    )
    assert own_case.search("存货", k=20)
    assert other_case.search("存货", k=20) == []

    real_case = ScopedRetriever(
        shared, {"policy", "public_reference", "case_history"},
        review_date="2026-10-24", **{**kwargs, "demo_case": False},
    )
    assert real_case.search("演示政策 GLEIF 历史审批", k=20) == []


def test_demo_catalog_hides_unreleased_and_case_history_from_analyst():
    module = _release_module()
    prepared = module["prepare_release"]()
    documents = [
        {**doc, "chunk_count": len(chunks), "updated_at": "2026-10-24"}
        for doc, chunks in prepared
    ]
    documents.append({
        "id": "legacy", "library_id": "policy", "title": "旧未版本化规则",
        "citation": "旧规则", "metadata": {"source_kind": "builtin"},
        "chunk_count": 1,
    })
    principal = {"user_id": "analyst", "name": "Analyst", "role": "credit_analyst"}
    catalog = knowledge_catalog(documents, principal, module["RELEASE_ID"])
    visible = [doc for library in catalog["libraries"] for doc in library["documents"]]
    assert len(visible) == 33
    assert all(doc["demo_only"] is True for doc in visible)
    assert all(doc["id"] != "legacy" for doc in visible)


def test_proposed_cards_are_readable_only_in_demo_case_and_release():
    module = _release_module()
    proposals = [item for item in module["prepare_release"]()
                 if item[0]["library_id"] in {"proposed_rule", "evidence_spec"}]
    assert len(proposals) == 30
    chunks = [
        Chunk(id=row["id"], text=row["text"], source=row["citation"],
              section=row["section"], meta={**row["metadata"], "library_id": doc["library_id"]})
        for doc, rows in proposals for row in rows
    ]
    shared = build_retriever(chunks, use_vector=False, use_rerank=False)
    query = "谁能批准额度 审批权限"
    allowed = ScopedRetriever(
        shared, {"proposed_rule", "evidence_spec"},
        demo_release_id=module["RELEASE_ID"], demo_case=True,
    )
    assert any("CRA-R21" in hit.chunk.citation for hit in allowed.search(query, k=10))
    assert ScopedRetriever(
        shared, {"proposed_rule", "evidence_spec"},
        demo_release_id=module["RELEASE_ID"], demo_case=False,
    ).search(query, k=10) == []
    assert ScopedRetriever(
        shared, {"proposed_rule", "evidence_spec"}, demo_case=True,
    ).search(query, k=10) == []
