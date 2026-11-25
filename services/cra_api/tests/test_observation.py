import json
from pathlib import Path
from types import SimpleNamespace

from cra import trace
from cra.config import get_settings


def test_trace_redacts_sensitive_payload_and_retains_operational_fields(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(
        trace, "get_settings", lambda: SimpleNamespace(logs_dir=tmp_path)
    )
    trace.log_event(
        "case_qa",
        {
            "case_id": "CASE-PRIVATE-123",
            "question": "该客户有未清应收 9876543 元吗？",
            "messages": [{"role": "user", "content": "SECRET CUSTOMER"}],
            "outcome": "missing_authoritative_data",
            "latency_s": 0.12,
        },
    )
    contents = next(tmp_path.glob("trace-*.jsonl")).read_text(encoding="utf-8")
    event = json.loads(contents)
    assert "CASE-PRIVATE-123" not in contents
    assert "SECRET CUSTOMER" not in contents
    assert "9876543" not in contents
    assert event["outcome"] == "missing_authoritative_data"
    assert event["question"]["sha256"]


def test_vercel_trace_path_uses_writable_runtime(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("CRA_RUNTIME_DIR", "")
    settings = get_settings.__wrapped__()

    assert settings.runtime_dir == Path("/tmp/cra-runtime")
    assert settings.logs_dir == settings.runtime_dir / "logs"


def test_trace_write_failure_does_not_break_workflow(tmp_path, monkeypatch, caplog):
    blocked = tmp_path / "unavailable-directory"
    blocked.touch()
    monkeypatch.setattr(
        trace, "get_settings", lambda: SimpleNamespace(logs_dir=blocked / "logs")
    )

    trace.log_event("llm_call", {"input": "PRIVATE FINANCIAL DATA"})

    assert "Trace write failed for llm_call" in caplog.text
    assert "PRIVATE FINANCIAL DATA" not in caplog.text
