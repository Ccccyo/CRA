"""Write bounded, metadata-only local events; never retain model source text."""
import hashlib
import json
import logging
import time
from datetime import datetime
from typing import Any, Dict

from cra.config import get_settings


logger = logging.getLogger(__name__)


SENSITIVE_KEYS = {
    "messages", "output", "input", "question", "query", "prompt",
    "content", "source_text", "api_key", "password", "secret", "case_id",
}


def _safe_value(key: str, value: Any) -> Any:
    if key.lower() in SENSITIVE_KEYS:
        serialized = json.dumps(value, ensure_ascii=False, default=str)
        return {
            "sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
            "chars": len(serialized),
        }
    if isinstance(value, dict):
        return {str(k): _safe_value(str(k), v) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe_value(key, item) for item in value[:100]]
    if isinstance(value, str) and len(value) > 500:
        return _safe_value("content", value)
    return value


def log_event(kind: str, payload: Dict[str, Any]) -> None:
    logs_dir = get_settings().logs_dir
    record = {
        "ts": time.time(), "kind": kind,
        **{key: _safe_value(key, value) for key, value in payload.items()},
    }
    path = logs_dir / f"trace-{datetime.now():%Y%m%d}.jsonl"
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except OSError as exc:
        logger.warning("Trace write failed for %s (%s)", kind, type(exc).__name__)
