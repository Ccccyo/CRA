import json
import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CATALOG = ROOT / "services/cra_api/data/knowledge/rule_evidence_v1/catalog.json"
SCRIPT = ROOT / "scripts/build-rule-evidence-staging.py"


def test_30_rule_evidence_documents_are_complete_and_source_mapped():
    pack = json.loads(CATALOG.read_text(encoding="utf-8"))
    cards = pack["documents"]
    sources = {source["id"] for source in pack["sources"]}
    assert len(cards) == 30
    assert len({card["id"] for card in cards}) == 30
    assert sum(card["kind"] == "rule" for card in cards) == 24
    assert sum(card["kind"] == "evidence" for card in cards) == 6
    for card in cards:
        assert set(card["basis"]) <= sources
        assert all(card[field].strip() for field in (
            "title", "question", "control", "evidence", "exception", "rationale", "owner",
        ))


def test_isolated_build_and_repeat_are_unpublished(tmp_path):
    database = tmp_path / "rule-evidence-staging.db"
    command = [sys.executable, str(SCRIPT), "--database", str(database)]
    first = json.loads(subprocess.run(command, check=True, capture_output=True, text=True).stdout)
    second = json.loads(subprocess.run(command, check=True, capture_output=True, text=True).stdout)
    assert first["changed_documents"] == 30
    assert first["documents"] == first["chunks"] == 30
    assert second["changed_documents"] == 0
    assert first["published_documents"] == second["published_documents"] == 0
    with sqlite3.connect(database) as connection:
        documents = connection.execute("SELECT library_id, source_content, metadata FROM knowledge_documents").fetchall()
        chunks = connection.execute("SELECT id, metadata FROM knowledge_chunks").fetchall()
    assert {row[0] for row in documents} == {"proposed_rule", "evidence_spec"}
    assert all("未获公司政策负责人批准" in row[1] for row in documents)
    assert all(json.loads(row[2])["status"] == "needs_human_review" for row in documents)
    assert all(json.loads(row[1])["is_published"] is False for row in chunks)
    from cra.retrieve.runtime import is_retrievable_knowledge
    assert all(not is_retrievable_knowledge(json.loads(row[2])) for row in documents)


def test_rejects_active_database_name(tmp_path):
    target = tmp_path / "workbench.db"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--database", str(target)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert not target.exists()


def test_sample_keyword_queries_find_the_relevant_card(tmp_path):
    database = tmp_path / "rule-evidence-staging.db"
    subprocess.run([sys.executable, str(SCRIPT), "--database", str(database)],
                   check=True, capture_output=True, text=True)
    from cra.ingest.chunker import Chunk
    from cra.retrieve.keyword import KeywordIndex
    from cra_api.store import WorkflowStore

    rows = WorkflowStore(database).knowledge_chunks()
    index = KeywordIndex([
        Chunk(id=row["id"], text=row["text"], source=row["citation"],
              section=row["section"], page=row["page"], meta=row["metadata"])
        for row in rows
    ])
    expected = {
        "OCR数字与原页冲突怎么办": "CRA-R10",
        "当前客户敞口和逾期从哪查": "CRA-R16",
        "谁能批准额度": "CRA-R21",
        "历史审批可否沿用": "CRA-E29",
    }
    for query, card_id in expected.items():
        hits = index.search(query, k=3)
        assert hits and hits[0].chunk.meta["card_id"] == card_id
