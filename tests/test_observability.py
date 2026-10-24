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


class TestAppInsightsReadyRaisesLoggerLevel:
    """Added in the Azure real-services audit pass: a REAL bug was found
    and fixed here -- configure_azure_monitor() attaches its exporter to
    the root logger but never raises any logger's effective level, and
    Python's root logger defaults to WARNING. record_ai_trace()/
    record_llm_call() both log at INFO, so every call was being silently
    dropped by Python's own logging module before Azure Monitor's
    handler ever saw it -- a deployment could have Application Insights
    fully configured (SDK init succeeds) and still send ZERO real
    telemetry. These tests pin the fix: successful configuration must
    raise the two telemetry loggers' levels to INFO."""

    def test_golden_successful_configuration_raises_logger_levels_to_info(self, monkeypatch):
        import logging

        for name in observability._TELEMETRY_LOGGER_NAMES:
            logging.getLogger(name).setLevel(logging.WARNING)  # reset to the real default this bug exhibited

        monkeypatch.setattr(observability, "_appinsights_state", None)
        monkeypatch.setattr(observability, "get_secret", lambda name: "InstrumentationKey=fake-for-test")

        class FakeConfigureModule:
            @staticmethod
            def configure_azure_monitor(**kwargs):
                return None

        import sys
        monkeypatch.setitem(sys.modules, "azure.monitor.opentelemetry", FakeConfigureModule())

        assert observability._appinsights_ready() is True
        for name in observability._TELEMETRY_LOGGER_NAMES:
            assert logging.getLogger(name).isEnabledFor(logging.INFO) is True

    def test_adversarial_without_the_fix_info_calls_would_be_silently_dropped(self):
        """Documents exactly what the bug looked like -- a logger at its
        Python default level silently discards .info() calls before any
        handler (including Azure Monitor's) ever runs. Uses an unrelated
        fresh logger name (not a child of cdpi.ai_trace/
        cdpi.llm_observability, which other tests in this class
        intentionally mutate) so this assertion is independent of test
        execution order."""
        import logging

        fresh_logger = logging.getLogger("some_unrelated_never_configured_logger_xyz")
        assert fresh_logger.isEnabledFor(logging.INFO) is False  # the exact condition that silently dropped every span

    def test_golden_not_configured_never_raises_logger_levels(self, monkeypatch):
        import logging

        for name in observability._TELEMETRY_LOGGER_NAMES:
            logging.getLogger(name).setLevel(logging.WARNING)
        monkeypatch.setattr(observability, "_appinsights_state", None)
        monkeypatch.setattr(observability, "get_secret", lambda name: None)

        assert observability._appinsights_ready() is False
        for name in observability._TELEMETRY_LOGGER_NAMES:
            assert logging.getLogger(name).isEnabledFor(logging.INFO) is False


class TestRunConnectivityTest:
    def test_golden_always_writes_to_local_log_with_a_real_correlation_id(self, tmp_path, monkeypatch):
        monkeypatch.setattr(observability, "TRACE_LOG_PATH", tmp_path / "ai_traces.jsonl")
        monkeypatch.setattr(observability, "_appinsights_ready", lambda: False)

        result = observability.run_connectivity_test()
        assert result["written_to_local_log"] is True
        assert result["correlation_id"].startswith("connectivity-test-")
        assert result["application_insights_configured"] is False
        content = (tmp_path / "ai_traces.jsonl").read_text(encoding="utf-8")
        assert result["correlation_id"] in content

    def test_golden_reports_handoff_when_appinsights_configured(self, tmp_path, monkeypatch):
        monkeypatch.setattr(observability, "TRACE_LOG_PATH", tmp_path / "ai_traces.jsonl")
        monkeypatch.setattr(observability, "_appinsights_ready", lambda: True)

        result = observability.run_connectivity_test()
        assert result["application_insights_handed_off"] is True

    def test_adversarial_never_claims_azure_ingestion_was_verified(self, tmp_path, monkeypatch):
        """The spec's explicit requirement: initializing/handing off to
        the SDK must never be reported as proof of real ingestion."""
        monkeypatch.setattr(observability, "TRACE_LOG_PATH", tmp_path / "ai_traces.jsonl")
        monkeypatch.setattr(observability, "_appinsights_ready", lambda: True)

        result = observability.run_connectivity_test()
        assert "does not confirm" in result["note"] or "does NOT confirm" in result["note"]

    def test_golden_distinct_correlation_id_each_call(self, tmp_path, monkeypatch):
        monkeypatch.setattr(observability, "TRACE_LOG_PATH", tmp_path / "ai_traces.jsonl")
        monkeypatch.setattr(observability, "_appinsights_ready", lambda: False)

        a = observability.run_connectivity_test()
        b = observability.run_connectivity_test()
        assert a["correlation_id"] != b["correlation_id"]
