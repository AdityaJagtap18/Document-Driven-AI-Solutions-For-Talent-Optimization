import json

import observability


class TestRecordLlmCall:
    def test_golden_writes_jsonl_record(self, tmp_path, monkeypatch):
        log_path = tmp_path / "llm_observability.jsonl"
        monkeypatch.setattr(observability, "LOG_PATH", log_path)
        monkeypatch.setattr(observability, "_appinsights_ready", lambda: False)

        record = observability.record_llm_call(
            label="test_prompt", model="gpt-3.5-turbo", latency_ms=123.456,
            prompt_tokens=10, completion_tokens=5, total_tokens=15,
            cost_usd=0.0001, success=True, retries=0,
        )

        assert log_path.exists()
        lines = log_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["label"] == "test_prompt"
        assert parsed["total_tokens"] == 15
        assert parsed["success"] is True
        assert record == parsed  # round-trips cleanly through JSON

    def test_golden_appends_multiple_calls(self, tmp_path, monkeypatch):
        log_path = tmp_path / "llm_observability.jsonl"
        monkeypatch.setattr(observability, "LOG_PATH", log_path)
        monkeypatch.setattr(observability, "_appinsights_ready", lambda: False)

        observability.record_llm_call(label="call_1", success=True)
        observability.record_llm_call(label="call_2", success=False, error="timeout")

        lines = log_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["label"] == "call_1"
        assert json.loads(lines[1])["label"] == "call_2"
        assert json.loads(lines[1])["error"] == "timeout"

    def test_edge_latency_none_not_rounded(self, tmp_path, monkeypatch):
        log_path = tmp_path / "llm_observability.jsonl"
        monkeypatch.setattr(observability, "LOG_PATH", log_path)
        monkeypatch.setattr(observability, "_appinsights_ready", lambda: False)

        record = observability.record_llm_call(label="no_latency")
        assert record["latency_ms"] is None

    def test_adversarial_appinsights_emit_failure_does_not_raise(self, tmp_path, monkeypatch):
        log_path = tmp_path / "llm_observability.jsonl"
        monkeypatch.setattr(observability, "LOG_PATH", log_path)
        monkeypatch.setattr(observability, "_appinsights_ready", lambda: True)

        class BrokenLoggingModule:
            @staticmethod
            def getLogger(name):
                raise RuntimeError("simulated App Insights failure")

        # scoped to observability.py's own `logging` reference only — does
        # NOT touch the real stdlib logging module pytest itself relies on
        monkeypatch.setattr(observability, "logging", BrokenLoggingModule())

        # must not raise, and the local log line must still be written
        observability.record_llm_call(label="still_logged_locally")
        assert log_path.exists()
        assert "still_logged_locally" in log_path.read_text(encoding="utf-8")
