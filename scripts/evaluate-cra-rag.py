#!/usr/bin/env python3
"""Read-only, reproducible evaluation of the published CRA demo RAG corpus.

This evaluates evidence availability and retrieval, not LLM answers or live RBAC.
Fixtures and no-answer cases remain visible as untested rather than disappearing
from the denominator of the overall test inventory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import runpy
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "services" / "cra_api"
sys.path.insert(0, str(API / "src"))

from cra.ingest.chunker import Chunk  # noqa: E402
from cra.retrieve.hybrid import build_retriever  # noqa: E402
from cra.retrieve.runtime import ScopedRetriever  # noqa: E402

DATASET = API / "data" / "knowledge" / "examples" / "rag_eval_cases.jsonl"
REPORT_SCHEMA_VERSION = "cra-rag-eval-v2"


def load_cases(path: Path = DATASET) -> list[dict]:
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate evaluation case ID")
    for case in cases:
        if case.get("split") not in {"dev", "holdout"}:
            raise ValueError(f"Invalid split: {case['id']}")
        if not isinstance(case.get("gold_sections"), list):
            raise ValueError(f"Missing gold_sections: {case['id']}")
    return cases


def first_failure(gold: set[str], corpus: set[str], allowed: set[str],
                  candidates: set[str], top: set[str]) -> str:
    if gold - corpus:
        return "source_missing"
    if gold - allowed:
        return "scope_or_publication"
    if gold - candidates:
        return "candidate_recall"
    if gold - top:
        return "top_k_ranking"
    return "retrieval_complete"


def evaluate(cases: list[dict] | None = None, k: int = 8) -> dict:
    if k < 1:
        raise ValueError("k must be positive")
    module = runpy.run_path(str(ROOT / "scripts" / "publish-demo-knowledge.py"))
    # Evaluate every released gold-source library. Proposed method/evidence
    # cards have their own release gate and are not gold sources for this
    # policy/case/public-reference dataset.
    prepared = [
        item for item in module["prepare_release"]()
        if item[0]["library_id"] in {
            "policy", "public_reference", "case_history", "case_evidence",
        }
    ]
    # Case evidence is indexed per authorized case at runtime and intentionally
    # omitted from the shared eight-document catalog. Keep the synthetic
    # CASE-003 fixture in this end-to-end evaluation so case-scoped retrieval is
    # still measured without publishing customer evidence as shared knowledge.
    staged = runpy.run_path(str(ROOT / "scripts" / "build-knowledge-staging.py"))[
        "prepare_documents"
    ]()
    for document, rows in staged:
        if document["metadata"].get("source_reference") != "knowledge/examples/demo_case_003_evidence.md":
            continue
        runtime_meta = {
            **document["metadata"],
            "status": "active_case_evidence",
            "is_published": True,
        }
        prepared.append((
            {**document, "metadata": runtime_meta},
            [
                {**row, "metadata": {**row["metadata"], **runtime_meta}}
                for row in rows
            ],
        ))
    chunks = []
    keys = {}
    digest = hashlib.sha256()
    for document, rows in prepared:
        for row in rows:
            chunk = Chunk(
                id=row["id"], text=row["text"], source=row["citation"],
                section=row["section"],
                meta={**row["metadata"], "library_id": document["library_id"]},
            )
            chunks.append(chunk)
            keys[chunk.id] = f"{document['source_filename']}#{row['metadata']['section_id']}"
            digest.update(chunk.id.encode("utf-8"))
            digest.update(chunk.text.encode("utf-8"))
    shared = build_retriever(chunks, use_vector=False, use_rerank=False)
    corpus = set(keys.values())
    evaluation_cases = cases if cases is not None else load_cases()
    dataset_bytes = DATASET.read_bytes() if cases is None else (
        "\n".join(json.dumps(case, ensure_ascii=False, sort_keys=True) for case in evaluation_cases)
        + "\n"
    ).encode("utf-8")
    details = []
    for case in evaluation_cases:
        background = case["background"]
        base = {"id": case["id"], "split": case["split"], "question": case["question"]}
        if background.get("fixture"):
            details.append({**base, "status": "fixture_not_installed", "first_failure": "not_run"})
            continue
        if background.get("authorized_case_ids") is not None and background.get("case_id") not in background["authorized_case_ids"]:
            details.append({**base, "status": "live_authorization_required", "first_failure": "not_run"})
            continue
        gold = set(case["gold_sections"])
        if not gold:
            details.append({**base, "status": "no_answer_generation_required", "first_failure": "not_run"})
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
        allowed = {keys[chunk.id] for chunk in chunks if scope._allowed(chunk)}
        candidate_hits = scope.candidates(case["question"], n=20)
        top_hits = scope.search(case["question"], k=k)
        candidate = {keys[hit.chunk.id] for hit in candidate_hits}
        top = {keys[hit.chunk.id] for hit in top_hits}
        failure = first_failure(gold, corpus, allowed, candidate, top)
        details.append({
            **base, "status": "scored", "first_failure": failure,
            "gold_sections": sorted(gold),
            "missing_from_corpus": sorted(gold - corpus),
            "filtered_by_scope": sorted((gold & corpus) - allowed),
            "missed_by_candidate": sorted((gold & allowed) - candidate),
            "missed_by_top_k": sorted((gold & candidate) - top),
            "top_k": [keys[hit.chunk.id] for hit in top_hits],
            "counts": {
                "gold": len(gold), "in_corpus": len(gold & corpus),
                "allowed": len(gold & allowed),
                "candidate_hit": len(gold & candidate), "top_k_hit": len(gold & top),
            },
        })
    scored = [row for row in details if row["status"] == "scored"]
    totals = {name: sum(row["counts"][name] for row in scored)
              for name in ("gold", "in_corpus", "allowed", "candidate_hit", "top_k_hit")}
    def rate(num: int, den: int) -> float | None:
        return round(num / den, 4) if den else None
    failures = Counter(row["first_failure"] for row in scored)
    badcases = [
        {
            "id": f"EVAL-{row['id']}", "eval_id": row["id"],
            "split": row["split"], "first_failure": row["first_failure"],
            "severity": "P0" if row["first_failure"] == "scope_or_publication" else "P1",
            "status": "open", "missing_from_corpus": row["missing_from_corpus"],
            "filtered_by_scope": row["filtered_by_scope"],
            "missed_by_candidate": row["missed_by_candidate"],
            "missed_by_top_k": row["missed_by_top_k"],
        }
        for row in scored if row["first_failure"] != "retrieval_complete"
    ]
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "scope": "published_mock_demo_offline_bm25_no_llm_no_live_rbac",
        "release_id": module["RELEASE_ID"], "corpus_sha256": digest.hexdigest(),
        "dataset": str(DATASET.relative_to(ROOT)),
        "dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
        "dataset_splits": Counter(row["split"] for row in details),
        "configuration": {
            "retriever": "bm25",
            "candidate_k": 20,
            "top_k": k,
            "vector_enabled": False,
            "rerank_enabled": False,
            "model": "not_applicable_retrieval_only",
            "prompt_version": "not_applicable_retrieval_only",
            "knowledge_release_id": module["RELEASE_ID"],
        },
        "metric_definitions": {
            "source_coverage": "gold sections present in published corpus / all gold sections",
            "scope_coverage": "gold sections allowed by pre-retrieval scope / all gold sections",
            "candidate_recall_at_20_over_available": "allowed gold sections found in 20 candidates / allowed gold sections",
            "gold_recall_at_k_over_all": "gold sections found in top-k / all gold sections",
            "gold_recall_at_k_over_available": "gold sections found in top-k / allowed gold sections",
            "complete_evidence_case_rate": "scored cases with every gold section in top-k / all scored cases",
        },
        "top_k": k,
        "documents": len(prepared), "chunks": len(chunks),
        "inventory": {"total": len(details), "scored": len(scored),
                      "unscored": Counter(row["status"] for row in details if row["status"] != "scored")},
        "totals": totals,
        "metrics": {
            "source_coverage": rate(totals["in_corpus"], totals["gold"]),
            "scope_coverage": rate(totals["allowed"], totals["gold"]),
            "candidate_recall_at_20_over_available": rate(totals["candidate_hit"], totals["allowed"]),
            "gold_recall_at_k_over_all": rate(totals["top_k_hit"], totals["gold"]),
            "gold_recall_at_k_over_available": rate(totals["top_k_hit"], totals["allowed"]),
            "complete_evidence_case_rate": rate(failures["retrieval_complete"], len(scored)),
        },
        "first_failure_counts": failures,
        "badcases": badcases,
        "release_gate": {
            "retrieval_evidence_complete": failures["retrieval_complete"] == len(scored),
            "answer_quality": "not_evaluated", "live_authorization": "not_evaluated",
            "decision": "not_ready" if failures["retrieval_complete"] != len(scored) else "requires_online_and_business_review",
        },
        "cases": details,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write JSON report to this explicit path")
    parser.add_argument("--top-k", type=int, default=8)
    args = parser.parse_args()
    report = evaluate(k=args.top_k)
    report["evaluated_at_utc"] = datetime.now(timezone.utc).isoformat()
    content = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding="utf-8")
    else:
        print(content, end="")


if __name__ == "__main__":
    main()
