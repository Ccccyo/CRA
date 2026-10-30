"""Regression checks for the offline demo RAG evaluation contract."""

import runpy
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "evaluate-cra-rag.py"


def _module():
    return runpy.run_path(str(SCRIPT))


def test_first_failure_attributes_the_earliest_stage():
    classify = _module()["first_failure"]
    gold = {"policy#one", "policy#two"}
    assert classify(gold, {"policy#one"}, {"policy#one"}, {"policy#one"}, {"policy#one"}) == "source_missing"
    assert classify(gold, gold, {"policy#one"}, {"policy#one"}, {"policy#one"}) == "scope_or_publication"
    assert classify(gold, gold, gold, {"policy#one"}, {"policy#one"}) == "candidate_recall"
    assert classify(gold, gold, gold, gold, {"policy#one"}) == "top_k_ranking"
    assert classify(gold, gold, gold, gold, gold) == "retrieval_complete"


def test_demo_evaluation_keeps_untested_cases_and_makes_no_answer_claims():
    report = _module()["evaluate"]()
    assert report["inventory"]["total"] == 13
    assert report["inventory"]["scored"] == 9
    assert report["inventory"]["unscored"]["fixture_not_installed"] == 2
    assert report["inventory"]["unscored"]["no_answer_generation_required"] == 2
    assert report["totals"]["gold"] == 22
    assert report["totals"]["top_k_hit"] <= report["totals"]["candidate_hit"]
    assert report["totals"]["candidate_hit"] <= report["totals"]["allowed"]
    assert report["release_gate"]["answer_quality"] == "not_evaluated"
    assert report["release_gate"]["live_authorization"] == "not_evaluated"
    case_by_id = {row["id"]: row for row in report["cases"]}
    assert case_by_id["KR-11"]["status"] == "no_answer_generation_required"
    assert case_by_id["KR-13"]["status"] == "fixture_not_installed"
