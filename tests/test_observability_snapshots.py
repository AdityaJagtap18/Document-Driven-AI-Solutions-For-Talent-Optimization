import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "context-lake"))

import observability_snapshots as snap  # noqa: E402
import repositories  # noqa: E402


class TestIsSoftUnavailable:
    def test_golden_success_with_available_false_is_soft_unavailable(self):
        result = {"status": "success", "data": {"available": False, "detail": "Log Analytics query failed."}}
        assert snap._is_soft_unavailable(result) is True

    def test_golden_success_with_available_true_is_not_soft_unavailable(self):
        result = {"status": "success", "data": {"available": True, "buckets": []}}
        assert snap._is_soft_unavailable(result) is False

    def test_adversarial_real_failure_is_not_soft_unavailable(self):
        result = {"status": "failed", "data": None}
        assert snap._is_soft_unavailable(result) is False

    def test_adversarial_success_with_non_dict_data_is_not_soft_unavailable(self):
        result = {"status": "success", "data": "not a dict"}
        assert snap._is_soft_unavailable(result) is False

    def test_edge_success_with_dict_lacking_available_key_is_not_soft_unavailable(self):
        # e.g. get_telemetry_health()'s shape (azure_configured/ingestion_confirmed,
        # no "available" key at all) -- never misclassified as soft-unavailable.
        result = {"status": "success", "data": {"azure_configured": True, "ingestion_confirmed": False}}
        assert snap._is_soft_unavailable(result) is False


class TestRunRefresh:
    """Real bug this covers: a source that returns NORMALLY (no raised
    exception) but whose own payload says {"available": False} (the real
    shape azure_monitor_query.py's functions use when a Log Analytics
    query fails) must NOT overwrite a prior real successful result --
    before this fix, it did, permanently erasing good data and never
    engaging the last_known_good fallback."""

    def test_golden_first_ever_soft_unavailable_has_no_last_known_good(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_latest_observability_snapshot", lambda: None)
        monkeypatch.setattr(repositories, "append_observability_refresh_result", lambda entry: entry)
        monkeypatch.setattr(repositories, "save_observability_snapshot", lambda s: s)
        monkeypatch.setattr(snap, "all_sources", lambda hours: {
            "azure_request_volume": lambda: {"available": False, "reason": "auth_failed", "detail": "Log Analytics query failed: expired TAP.", "buckets": []},
        })

        result = snap.run_refresh("test", hours=24)
        source = result["sources"]["azure_request_volume"]
        assert source["status"] == "failed"
        assert source["last_known_good"] is None
        assert "expired TAP" in source["error"]
        assert result["failed_count"] == 1
        assert result["succeeded_count"] == 0

    def test_golden_soft_unavailable_preserves_prior_real_success(self, monkeypatch):
        real_prior_data = {"available": True, "detail": "Real Log Analytics query, 12 row(s).", "buckets": [{"timestamp": "t1", "total": 12, "succeeded": 10, "failed": 2}]}
        previous_snapshot = {
            "sources": {
                "azure_request_volume": {
                    "status": "success", "data": real_prior_data,
                    "collected_at": "2026-10-20T00:00:00Z", "collected_by_refresh_run_id": "old-run",
                },
            },
        }
        monkeypatch.setattr(repositories, "get_latest_observability_snapshot", lambda: previous_snapshot)
        monkeypatch.setattr(repositories, "append_observability_refresh_result", lambda entry: entry)
        monkeypatch.setattr(repositories, "save_observability_snapshot", lambda s: s)
        monkeypatch.setattr(snap, "all_sources", lambda hours: {
            "azure_request_volume": lambda: {"available": False, "reason": "auth_failed", "detail": "Log Analytics query failed: expired TAP (this later refresh).", "buckets": []},
        })

        result = snap.run_refresh("test", hours=24)
        source = result["sources"]["azure_request_volume"]
        assert source["status"] == "failed"
        # the REAL prior successful data is preserved, not erased
        assert source["last_known_good"]["status"] == "success"
        assert source["last_known_good"]["data"] == real_prior_data
        assert "this later refresh" in source["error"]

    def test_golden_a_real_success_replaces_both_the_live_data_and_any_stale_last_known_good(self, monkeypatch):
        previous_snapshot = {
            "sources": {
                "azure_request_volume": {
                    "status": "failed", "error": "old failure",
                    "last_known_good": {"status": "success", "data": {"available": True, "buckets": [{"timestamp": "old", "total": 1, "succeeded": 1, "failed": 0}]}},
                },
            },
        }
        monkeypatch.setattr(repositories, "get_latest_observability_snapshot", lambda: previous_snapshot)
        monkeypatch.setattr(repositories, "append_observability_refresh_result", lambda entry: entry)
        monkeypatch.setattr(repositories, "save_observability_snapshot", lambda s: s)
        new_real_data = {"available": True, "detail": "Real query, 3 rows.", "buckets": [{"timestamp": "new", "total": 3, "succeeded": 3, "failed": 0}]}
        monkeypatch.setattr(snap, "all_sources", lambda hours: {"azure_request_volume": lambda: new_real_data})

        result = snap.run_refresh("test", hours=24)
        source = result["sources"]["azure_request_volume"]
        assert source["status"] == "success"
        assert source["data"] == new_real_data
        assert result["succeeded_count"] == 1
        assert result["failed_count"] == 0

    def test_adversarial_a_genuine_exception_still_preserves_last_known_good_as_before(self, monkeypatch):
        previous_snapshot = {
            "sources": {
                "service_health": {"status": "success", "data": {"services": [{"service_name": "Cosmos DB", "status": "READY"}]}, "collected_at": "x"},
            },
        }
        monkeypatch.setattr(repositories, "get_latest_observability_snapshot", lambda: previous_snapshot)
        monkeypatch.setattr(repositories, "append_observability_refresh_result", lambda entry: entry)
        monkeypatch.setattr(repositories, "save_observability_snapshot", lambda s: s)

        def broken():
            raise RuntimeError("Cosmos unavailable")

        monkeypatch.setattr(snap, "all_sources", lambda hours: {"service_health": broken})

        result = snap.run_refresh("test", hours=24)
        source = result["sources"]["service_health"]
        assert source["status"] == "failed"
        assert source["last_known_good"]["data"]["services"][0]["service_name"] == "Cosmos DB"
        assert "Cosmos unavailable" in source["error"]

    def test_edge_not_configured_source_is_not_treated_as_soft_unavailable(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_latest_observability_snapshot", lambda: None)
        monkeypatch.setattr(repositories, "append_observability_refresh_result", lambda entry: entry)
        monkeypatch.setattr(repositories, "save_observability_snapshot", lambda s: s)

        def not_configured_source():
            return {"available": False, "reason": "not_configured", "detail": "not set", "_not_configured": True, "buckets": []}

        monkeypatch.setattr(snap, "all_sources", lambda hours: {"azure_request_volume": not_configured_source})

        result = snap.run_refresh("test", hours=24)
        source = result["sources"]["azure_request_volume"]
        assert source["status"] == "not_configured"
        assert result["skipped_count"] == 1
        assert result["failed_count"] == 0

    def test_golden_telemetry_health_shape_without_available_key_merges_as_a_real_success(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_latest_observability_snapshot", lambda: None)
        monkeypatch.setattr(repositories, "append_observability_refresh_result", lambda entry: entry)
        monkeypatch.setattr(repositories, "save_observability_snapshot", lambda s: s)
        health_data = {"azure_configured": True, "ingestion_confirmed": False, "last_ingested_at": None, "detail": "no rows in window"}
        monkeypatch.setattr(snap, "all_sources", lambda hours: {"azure_telemetry_health": lambda: health_data})

        result = snap.run_refresh("test", hours=24)
        source = result["sources"]["azure_telemetry_health"]
        assert source["status"] == "success"
        assert source["data"] == health_data
        assert result["succeeded_count"] == 1
