#!/usr/bin/env python3
"""Check whether versioned external knowledge is due for source verification.

This command is read-only. It does not fetch or publish content; a knowledge
owner must review the source, update the immutable snapshot and publish a new
release before the refreshed material can enter retrieval.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "services/cra_api/data/knowledge/examples/ingest_manifest.json"


def freshness_rows(as_of: date) -> list[dict]:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rows = []
    for item in payload["documents"]:
        verified_at = item.get("verified_at")
        interval = item.get("refresh_interval_days")
        if not verified_at or not interval:
            continue
        verified = datetime.strptime(verified_at, "%Y-%m-%d").date()
        due = verified + timedelta(days=int(interval))
        rows.append({
            "source_path": item["source_path"],
            "document_version": item.get("document_version", ""),
            "verified_at": verified.isoformat(),
            "next_review_at": due.isoformat(),
            "status": "overdue" if as_of > due else "current",
        })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", default=date.today().isoformat())
    args = parser.parse_args()
    as_of = datetime.strptime(args.as_of, "%Y-%m-%d").date()
    rows = freshness_rows(as_of)
    print(json.dumps({"as_of": as_of.isoformat(), "sources": rows}, ensure_ascii=False))
    if any(row["status"] == "overdue" for row in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
