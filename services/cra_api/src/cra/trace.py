"""每次大模型调用写一行 JSONL，用于排查坏例子、统计成本和延迟。"""
import json
import time
from datetime import datetime
from typing import Any, Dict

from cra.config import get_settings


def log_event(kind: str, payload: Dict[str, Any]) -> None:
    logs_dir = get_settings().logs_dir
    logs_dir.mkdir(parents=True, exist_ok=True)
    record = {"ts": time.time(), "kind": kind, **payload}
    path = logs_dir / f"trace-{datetime.now():%Y%m%d}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
