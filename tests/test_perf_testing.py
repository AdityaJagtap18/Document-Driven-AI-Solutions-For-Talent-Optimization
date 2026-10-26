import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import perf_testing  # noqa: E402


class TestComputePercentiles:
    def test_golden_computes_percentiles_over_real_values(self):
        latencies = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
        result = perf_testing.compute_percentiles(latencies)
        assert result["sample_size"] == 10
        assert result["min"] == 10.0
        assert result["max"] == 100.0
        assert result["avg"] == 55.0
        assert result["p50"] is not None and result["p95"] is not None and result["p99"] is not None

    def test_edge_empty_list_reports_none_not_zero(self):
        result = perf_testing.compute_percentiles([])
        assert result["p50"] is None
        assert result["sample_size"] == 0

    def test_edge_single_value(self):
        result = perf_testing.compute_percentiles([42.0])
        assert result["p50"] == 42.0
        assert result["p95"] == 42.0
        assert result["p99"] == 42.0
        assert result["sample_size"] == 1

    def test_golden_p99_is_at_least_p95_is_at_least_p50(self):
        latencies = [i * 1.0 for i in range(1, 101)]
        result = perf_testing.compute_percentiles(latencies)
        assert result["p50"] <= result["p95"] <= result["p99"]


class TestSummarizeRun:
    def test_golden_all_succeeded(self):
        results = [
            {"success": True, "latency_ms": 100.0, "started_at": 0.0, "completed_at": 1.0, "error": None},
            {"success": True, "latency_ms": 200.0, "started_at": 0.5, "completed_at": 1.5, "error": None},
        ]
        summary = perf_testing.summarize_run(results)
        assert summary["total_requests"] == 2
        assert summary["succeeded"] == 2
        assert summary["failed"] == 0
        assert summary["failure_rate"] == 0.0
        assert summary["latency_ms"]["sample_size"] == 2

    def test_golden_mixed_success_and_failure(self):
        results = [
            {"success": True, "latency_ms": 100.0, "started_at": 0.0, "completed_at": 1.0, "error": None},
            {"success": False, "latency_ms": 5000.0, "started_at": 0.0, "completed_at": 5.0, "error": "HTTP 500"},
        ]
        summary = perf_testing.summarize_run(results)
        assert summary["total_requests"] == 2
        assert summary["succeeded"] == 1
        assert summary["failed"] == 1
        assert summary["failure_rate"] == 0.5
        # a failed request's latency must not pollute the success-latency percentiles
        assert summary["latency_ms"]["sample_size"] == 1
        assert summary["latency_ms"]["max"] == 100.0
        assert summary["errors"] == ["HTTP 500"]

    def test_edge_empty_results(self):
        summary = perf_testing.summarize_run([])
        assert summary["total_requests"] == 0
        assert summary["failure_rate"] is None
        assert summary["throughput_rps"] is None

    def test_golden_throughput_computed_from_real_wall_clock_span(self):
        results = [
            {"success": True, "latency_ms": 10.0, "started_at": 0.0, "completed_at": 1.0, "error": None},
            {"success": True, "latency_ms": 10.0, "started_at": 0.5, "completed_at": 2.0, "error": None},
        ]
        summary = perf_testing.summarize_run(results)
        # span = max(completed_at) - min(started_at) = 2.0 - 0.0 = 2.0s, 2 requests -> 1.0 rps
        assert summary["throughput_rps"] == 1.0

    def test_adversarial_zero_duration_span_does_not_divide_by_zero(self):
        results = [{"success": True, "latency_ms": 1.0, "started_at": 0.0, "completed_at": 0.0, "error": None}]
        summary = perf_testing.summarize_run(results)
        assert summary["throughput_rps"] is None


class TestRunLoadScenario:
    def test_adversarial_unknown_scenario_rejected(self):
        with pytest.raises(ValueError, match="not a safe, allowlisted"):
            perf_testing.run_load_scenario("http://localhost:7071", "delete_everything", 10, 5)

    def test_adversarial_auth_required_scenario_without_token_rejected(self):
        with pytest.raises(ValueError, match="requires a real auth_token"):
            perf_testing.run_load_scenario("http://localhost:7071", "candidates_list", 10, 5)

    def test_golden_health_scenario_does_not_require_auth(self, monkeypatch):
        calls = []

        def fake_make_request(base_url, scenario, headers, epoch):
            calls.append(headers)
            return {"success": True, "status_code": 200, "latency_ms": 5.0, "started_at": 0.0, "completed_at": 0.01, "error": None}

        monkeypatch.setattr(perf_testing, "_make_request", fake_make_request)
        results = perf_testing.run_load_scenario("http://localhost:7071", "health", 3, 2)
        assert len(results) == 3
        assert all(h == {} for h in calls)

    def test_golden_auth_token_is_passed_as_bearer_header(self, monkeypatch):
        captured = {}

        def fake_make_request(base_url, scenario, headers, epoch):
            captured["headers"] = headers
            return {"success": True, "status_code": 200, "latency_ms": 5.0, "started_at": 0.0, "completed_at": 0.01, "error": None}

        monkeypatch.setattr(perf_testing, "_make_request", fake_make_request)
        perf_testing.run_load_scenario("http://localhost:7071", "candidates_list", 1, 1, auth_token="tok123")
        assert captured["headers"] == {"Authorization": "Bearer tok123"}

    def test_adversarial_num_requests_is_hard_capped(self, monkeypatch):
        calls = []
        monkeypatch.setattr(perf_testing, "_make_request", lambda *a, **k: calls.append(1) or {"success": True, "status_code": 200, "latency_ms": 1.0, "started_at": 0.0, "completed_at": 0.001, "error": None})
        perf_testing.run_load_scenario("http://localhost:7071", "health", perf_testing.DEFAULT_MAX_REQUESTS + 500, 5)
        assert len(calls) == perf_testing.DEFAULT_MAX_REQUESTS

    def test_adversarial_concurrency_is_hard_capped(self, monkeypatch):
        # Can't directly observe ThreadPoolExecutor's max_workers from
        # outside, but confirm an absurd concurrency request doesn't
        # error and the run still completes with the expected count.
        monkeypatch.setattr(perf_testing, "_make_request", lambda *a, **k: {"success": True, "status_code": 200, "latency_ms": 1.0, "started_at": 0.0, "completed_at": 0.001, "error": None})
        results = perf_testing.run_load_scenario("http://localhost:7071", "health", 10, 99999)
        assert len(results) == 10

    def test_golden_returns_one_result_per_request(self, monkeypatch):
        monkeypatch.setattr(perf_testing, "_make_request", lambda *a, **k: {"success": True, "status_code": 200, "latency_ms": 1.0, "started_at": 0.0, "completed_at": 0.001, "error": None})
        results = perf_testing.run_load_scenario("http://localhost:7071", "health", 7, 3)
        assert len(results) == 7
