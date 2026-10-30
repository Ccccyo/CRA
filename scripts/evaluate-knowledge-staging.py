#!/usr/bin/env python3
"""Offline, mock-inclusive BM25 diagnostic for the isolated CRA knowledge set.

This intentionally does not assess live authorization or generated answers.
"""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = PROJECT_ROOT / "services" / "cra_api"
sys.path.insert(0, str(API_ROOT / "src"))

from cra.ingest.chunker import Chunk  # noqa: E402
from cra.retrieve.hybrid import build_retriever  # noqa: E402


def applicable(metadata: dict, background: dict) -> bool:
    kind = metadata.get("source_kind", "")
    if kind == "mock_case_evidence_summary":
        return (
            metadata.get("case_id") == background.get("case_id")
            and background.get("case_id") in background.get(
                "authorized_case_ids", [background.get("case_id")]
            )
        )
    if kind == "mock_case_history_summary":
        return bool(background.get("bp")) and metadata.get("bp") == background.get("bp")
    if kind in {"mock_policy", "mock_scoring_description"}:
        if metadata.get("region") != background.get("region", "ALL_DEMO"):
            return False
        if metadata.get("credit_segment") != background.get("credit_segment", "0001"):
            return False
        as_of = background.get("as_of")
        if not as_of:
            return False
        return not (
            metadata.get("effective_from") and as_of < metadata["effective_from"]
            or metadata.get("effective_to") and as_of > metadata["effective_to"]
        )
    return True


def evaluate() -> dict:
    prepared = runpy.run_path(
        str(PROJECT_ROOT / "scripts" / "build-knowledge-staging.py")
    )["prepare_documents"]()
    cases = [
        json.loads(line)
        for line in (
            API_ROOT / "data" / "knowledge" / "examples" / "rag_eval_cases.jsonl"
        ).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    results = []
    for case in cases:
        if case["background"].get("fixture"):
            results.append({"id": case["id"], "status": "fixture_not_installed"})
            continue
        gold = set(case["gold_sections"])
        if not gold:
            results.append({"id": case["id"], "status": "no_answer_case_not_scored"})
            continue
        corpus = []
        for document, rows in prepared:
            if not applicable(document["metadata"], case["background"]):
                continue
            for row in rows:
                key = f"{document['source_filename']}#{row['metadata']['section_id']}"
                corpus.append(
                    Chunk(
                        id=key, text=row["text"], source=document["citation"],
                        section=row["section"], meta=document["metadata"],
                    )
                )
        available = gold & {chunk.id for chunk in corpus}
        retriever = build_retriever(corpus, use_vector=False, use_rerank=False)
        hits = retriever.search(case["question"], k=8)
        hit_ids = {item.chunk.id for item in hits}
        results.append({
            "id": case["id"], "split": case["split"],
            "gold_total": len(gold), "gold_available": len(available),
            "gold_hit_at_8": len(gold & hit_ids),
            "missing_gold": sorted(gold - available),
        })
    scored = [item for item in results if "gold_total" in item]
    total = sum(item["gold_total"] for item in scored)
    available = sum(item["gold_available"] for item in scored)
    hit = sum(item["gold_hit_at_8"] for item in scored)
    return {
        "scope": "isolated_mock_inclusive_offline_bm25_only",
        "cases_total": len(cases), "scored_cases": len(scored),
        "fixture_excluded": sum(item["status"] == "fixture_not_installed" for item in results if "status" in item),
        "no_answer_not_scored": sum(item["status"] == "no_answer_case_not_scored" for item in results if "status" in item),
        "gold_sections": total, "gold_available": available,
        "gold_hit_at_8": hit,
        "coverage": round(available / total, 4) if total else None,
        "recall_at_8": round(hit / total, 4) if total else None,
        "per_case": results,
    }


if __name__ == "__main__":
    print(json.dumps(evaluate(), ensure_ascii=False, indent=2))
