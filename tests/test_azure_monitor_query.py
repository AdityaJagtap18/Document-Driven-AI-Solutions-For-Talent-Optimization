"""Unit tests for azure_monitor_query.py -- all Azure SDK calls are
mocked/monkeypatched (clearly labeled below); no test here makes a real
network call. See docs/AZURE_OBSERVABILITY.md for how this is verified
against the real Azure Monitor workspace separately."""

import azure_monitor_query as amq


class TestIsConfigured:
    def test_golden_configured_when_workspace_id_set(self, monkeypatch):
        monkeypatch.setenv("AZURE_LOG_ANALYTICS_WORKSPACE_ID", "fake-workspace-guid")
        assert amq.is_configured() is True

    def test_edge_not_configured_when_unset(self, monkeypatch):
        monkeypatch.delenv("AZURE_LOG_ANALYTICS_WORKSPACE_ID", raising=False)
        assert amq.is_configured() is False

    def test_edge_not_configured_when_blank(self, monkeypatch):
        monkeypatch.setenv("AZURE_LOG_ANALYTICS_WORKSPACE_ID", "   ")
        assert amq.is_configured() is False


class TestRunKqlQueryNotConfigured:
    def test_golden_unconfigured_returns_unavailable_never_raises(self, monkeypatch):
        monkeypatch.delenv("AZURE_LOG_ANALYTICS_WORKSPACE_ID", raising=False)
        result = amq.run_kql_query("traces | take 1")
        assert result["available"] is False
        assert result["reason"] == "not_configured"
        assert result["rows"] == []
        assert result["columns"] == []


class TestRunKqlQueryClientUnavailable:
    """Mocked: simulates azure-monitor-query not being importable/client
    construction failing, without needing the real package installed."""

    def test_adversarial_dependency_missing_fails_closed(self, monkeypatch):
        monkeypatch.setenv("AZURE_LOG_ANALYTICS_WORKSPACE_ID", "fake-workspace-guid")
        monkeypatch.setattr(amq, "_logs_client", lambda: (None, "dependency_missing"))
        result = amq.run_kql_query("traces | take 1")
        assert result["available"] is False
        assert result["reason"] == "dependency_missing"
        assert result["rows"] == []


class _FakeColumn:
    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return self.name == other

    def __hash__(self):
        return hash(self.name)

    def __repr__(self):
        return self.name


class _FakeTable:
    def __init__(self, columns, rows):
        self.columns = columns
        self.rows = rows


class _FakeLogsQueryResponse:
    def __init__(self, status, tables=None, partial_error=None):
        self.status = status
        self.tables = tables or []
        self.partial_error = partial_error


class _FakeLogsClient:
    """Mocked azure.monitor.query.LogsQueryClient -- returns a
    pre-built response instead of making a real HTTP call."""

    def __init__(self, response=None, exception=None):
        self._response = response
        self._exception = exception

    def query_workspace(self, workspace_id, kql, timespan=None, server_timeout=None):
        if self._exception:
            raise self._exception
        return self._response


class TestRunKqlQuerySuccess:
    def test_golden_successful_query_returns_real_shaped_rows(self, monkeypatch):
        monkeypatch.setenv("AZURE_LOG_ANALYTICS_WORKSPACE_ID", "fake-workspace-guid")
        import sys
        import types
        fake_module = types.SimpleNamespace(SUCCESS="Success")
        monkeypatch.setitem(sys.modules, "azure.monitor.query", types.SimpleNamespace(LogsQueryStatus=fake_module))

        table = _FakeTable(["timestamp", "total"], [["2026-01-01T00:00:00Z", 5]])
        response = _FakeLogsQueryResponse(status="Success", tables=[table])
        monkeypatch.setattr(amq, "_logs_client", lambda: (_FakeLogsClient(response=response), None))

        result = amq.run_kql_query("traces | take 1")
        assert result["available"] is True
        assert result["columns"] == ["timestamp", "total"]
        assert result["rows"] == [["2026-01-01T00:00:00Z", 5]]

    def test_edge_empty_result_table_still_available(self, monkeypatch):
        monkeypatch.setenv("AZURE_LOG_ANALYTICS_WORKSPACE_ID", "fake-workspace-guid")
        import sys
        import types
        fake_module = types.SimpleNamespace(SUCCESS="Success")
        monkeypatch.setitem(sys.modules, "azure.monitor.query", types.SimpleNamespace(LogsQueryStatus=fake_module))
        response = _FakeLogsQueryResponse(status="Success", tables=[])
        monkeypatch.setattr(amq, "_logs_client", lambda: (_FakeLogsClient(response=response), None))

        result = amq.run_kql_query("traces | take 1")
        assert result["available"] is True
        assert result["rows"] == []

    def test_adversarial_query_exception_categorized_not_fabricated(self, monkeypatch):
        monkeypatch.setenv("AZURE_LOG_ANALYTICS_WORKSPACE_ID", "fake-workspace-guid")
        import sys
        import types
        fake_module = types.SimpleNamespace(SUCCESS="Success")
        monkeypatch.setitem(sys.modules, "azure.monitor.query", types.SimpleNamespace(LogsQueryStatus=fake_module))
        monkeypatch.setattr(amq, "_logs_client", lambda: (_FakeLogsClient(exception=RuntimeError("boom")), None))

        result = amq.run_kql_query("traces | take 1")
        assert result["available"] is False
        assert result["rows"] == []
        assert "boom" in result["detail"]


class TestGetTelemetryHealth:
    def test_golden_ingestion_confirmed(self, monkeypatch):
        monkeypatch.setattr(amq, "run_kql_query", lambda kql, hours=24: {
            "available": True, "columns": ["last_ingested"], "rows": [["2026-01-01T00:00:00Z"]],
            "reason": None, "detail": "ok",
        })
        result = amq.get_telemetry_health()
        assert result["ingestion_confirmed"] is True
        assert result["last_ingested_at"] == "2026-01-01T00:00:00Z"
        assert result["azure_configured"] is True

    def test_edge_configured_but_no_rows(self, monkeypatch):
        monkeypatch.setattr(amq, "run_kql_query", lambda kql, hours=24: {
            "available": True, "columns": ["last_ingested"], "rows": [[None]],
            "reason": None, "detail": "ok",
        })
        result = amq.get_telemetry_health()
        assert result["ingestion_confirmed"] is False
        assert result["reason"] == "no_rows_in_window"

    def test_adversarial_query_unavailable_propagates_reason(self, monkeypatch):
        monkeypatch.setattr(amq, "run_kql_query", lambda kql, hours=24: {
            "available": False, "columns": [], "rows": [], "reason": "authentication_failed", "detail": "nope",
        })
        result = amq.get_telemetry_health()
        assert result["ingestion_confirmed"] is False
        assert result["reason"] == "authentication_failed"
        assert result["last_ingested_at"] is None


class TestGetRequestVolume:
    def test_golden_buckets_shaped_correctly(self, monkeypatch):
        monkeypatch.setattr(amq, "run_kql_query", lambda kql, hours=24: {
            "available": True,
            "columns": ["timestamp", "total", "succeeded", "failed"],
            "rows": [["2026-01-01T00:00:00Z", 10, 9, 1]],
            "reason": None, "detail": "ok",
        })
        result = amq.get_request_volume(hours=24)
        assert result["available"] is True
        assert result["buckets"] == [{"timestamp": "2026-01-01T00:00:00Z", "total": 10, "succeeded": 9, "failed": 1}]

    def test_edge_unavailable_returns_empty_buckets_not_fabricated(self, monkeypatch):
        monkeypatch.setattr(amq, "run_kql_query", lambda kql, hours=24: {
            "available": False, "columns": [], "rows": [], "reason": "not_configured", "detail": "x",
        })
        result = amq.get_request_volume(hours=24)
        assert result["available"] is False
        assert result["buckets"] == []


class TestGetLatencyPercentiles:
    def test_golden_sufficient_sample_returns_percentiles(self, monkeypatch):
        monkeypatch.setattr(amq, "run_kql_query", lambda kql, hours=24: {
            "available": True, "columns": ["count", "p50", "p95", "p99"],
            "rows": [[50, 100.0, 250.0, 400.0]], "reason": None, "detail": "ok",
        })
        result = amq.get_latency_percentiles(hours=24)
        assert result["available"] is True
        assert result["p50"] == 100.0
        assert result["p95"] == 250.0

    def test_edge_insufficient_sample_does_not_report_a_percentile(self, monkeypatch):
        monkeypatch.setattr(amq, "run_kql_query", lambda kql, hours=24: {
            "available": True, "columns": ["count", "p50", "p95", "p99"],
            "rows": [[5, 100.0, 250.0, 400.0]], "reason": None, "detail": "ok",
        })
        result = amq.get_latency_percentiles(hours=24)
        assert result["available"] is True
        assert result["p95"] is None
        assert "too few" in result["detail"]

    def test_edge_zero_rows_returns_zero_sample_not_crash(self, monkeypatch):
        monkeypatch.setattr(amq, "run_kql_query", lambda kql, hours=24: {
            "available": True, "columns": ["count", "p50", "p95", "p99"], "rows": [],
            "reason": None, "detail": "ok",
        })
        result = amq.get_latency_percentiles(hours=24)
        assert result["sample_size"] == 0
        assert result["p95"] is None


class TestGetAiUsage:
    def test_golden_by_model_breakdown(self, monkeypatch):
        monkeypatch.setattr(amq, "run_kql_query", lambda kql, hours=24: {
            "available": True, "columns": ["model", "provider", "calls", "total_tokens", "total_cost_usd", "failed"],
            "rows": [["gpt-4o", "azure_openai", 12, 3400.0, 0.085, 1]], "reason": None, "detail": "ok",
        })
        result = amq.get_ai_usage(hours=24)
        assert result["available"] is True
        assert result["by_model"][0]["model"] == "gpt-4o"
        assert result["by_model"][0]["calls"] == 12


class TestGetRecentErrors:
    def test_golden_errors_shaped_and_capped(self, monkeypatch):
        monkeypatch.setattr(amq, "run_kql_query", lambda kql, hours=24: {
            "available": True,
            "columns": ["timestamp", "message", "workflow", "stage", "label", "error", "correlation_id"],
            "rows": [["2026-01-01T00:00:00Z", "ai_trace", "resume_parse", "extraction", None, "boom", "corr-1"]],
            "reason": None, "detail": "ok",
        })
        result = amq.get_recent_errors(hours=24, limit=5)
        assert result["available"] is True
        assert result["errors"][0]["correlation_id"] == "corr-1"

    def test_edge_limit_is_clamped(self, monkeypatch):
        captured = {}

        def fake_query(kql, hours=24):
            captured["kql"] = kql
            return {"available": True, "columns": [], "rows": [], "reason": None, "detail": "ok"}

        monkeypatch.setattr(amq, "run_kql_query", fake_query)
        amq.get_recent_errors(hours=24, limit=9999)
        assert "take 100" in captured["kql"]
