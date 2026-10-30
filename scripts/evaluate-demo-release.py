#!/usr/bin/env python3
"""Offline evidence-retrieval diagnostic for the curated demo release.

This does not call a model or prove answer quality. Gold sections absent from
the five-document release remain in the denominator and are reported.
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
from cra.retrieve.runtime import ScopedRetriever  # noqa: E402


def evaluate() -> dict:
    module = runpy.run_path(str(PROJECT_ROOT / "scripts" / "publish-demo-knowledge.py"))
    prepared = module["prepare_release"]()
    chunks = []
    keys = {}
    for document, rows in prepared:
        for row in rows:
            chunk = Chunk(
                id=row["id"], text=row["text"], source=row["citation"],
                section=row["section"], meta={
                    **row["metadata"], "library_id": document["library_id"],
                },
            )
            chunks.append(chunk)
            keys[chunk.id] = (
                f"{document['source_filename']}#{row['metadata']['section_id']}"
            )
    shared = build_retriever(chunks, use_vector=False, use_rerank=False)
    cases = [
        json.loads(line) for line in (
            API_ROOT / "data" / "knowledge" / "examples" / "rag_eval_cases.jsonl"
        ).read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    details = []
    for case in cases:
        background = case["background"]
        if background.get("fixture"):
            details.append({"id": case["id"], "status": "fixture_not_installed"})
            continue
        scope = ScopedRetriever(
            shared, {"policy", "public_reference", "case_history"},
            case_id=background.get("case_id", ""),
            review_date=background.get("as_of", ""),
            credit_segment=background.get("credit_segment", "0001"),
            region=background.get("region", "ALL_DEMO"),
            bp=background.get("bp", ""),
            demo_release_id=module["RELEASE_ID"], demo_case=True,
        )
        available = {keys[item.id] for item in chunks if scope._allowed(item)}
        hits = scope.search(case["question"], k=8)
        hit_keys = {keys[hit.chunk.id] for hit in hits}
        gold = set(case["gold_sections"])
        if not gold:
            details.append({
                "id": case["id"], "status": "no_answer_case",
                "retrieved": sorted(hit_keys),
            })
            continue
        details.append({
            "id": case["id"], "split": case["split"],
            "gold_total": len(gold), "gold_available": len(gold & available),
            "gold_hit_at_8": len(gold & hit_keys),
            "unavailable_gold": sorted(gold - available),
        })
    scored = [row for row in details if "gold_total" in row]
    gold_total = sum(row["gold_total"] for row in scored)
    available_total = sum(row["gold_available"] for row in scored)
    hit_total = sum(row["gold_hit_at_8"] for row in scored)
    return {
        "scope": "curated_demo_release_offline_bm25_no_generation",
        "release_id": module["RELEASE_ID"], "documents": len(prepared),
        "chunks": len(chunks), "scored_cases": len(scored),
        "gold_total": gold_total, "gold_available": available_total,
        "gold_hit_at_8": hit_total,
        "source_coverage": round(available_total / gold_total, 4) if gold_total else None,
        "gold_hit_rate_at_8": round(hit_total / gold_total, 4) if gold_total else None,
        "per_case": details,
    }


if __name__ == "__main__":
    print(json.dumps(evaluate(), ensure_ascii=False, indent=2))
