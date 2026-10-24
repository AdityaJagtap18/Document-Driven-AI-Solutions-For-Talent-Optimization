"""Tests for the AI Observability Center and Azure Service Hub:
observability.py's record_ai_trace()/trace_stage(), azure_integrations.py's
registry/check_connection()/redact_secret(), and the
api_blueprint.py admin/ai-observability* and admin/azure-services*
endpoints (monkeypatched the usual way, same pattern as every other
endpoint test in this suite)."""

import json
import time

import azure.functions as func
import pytest

import api_blueprint as api
import auth
import azure_integrations
import model_pricing
import observability
import repositories
from conftest import _req, _fixture_token, _FIXTURE_ADMIN, _FIXTURE_ADMIN_ROLE


# ---------------------------------------------------------------------------
# observability.py -- record_ai_trace() / trace_stage()
# ---------------------------------------------------------------------------
class TestRecordAiTrace:
    def test_golden_writes_one_jsonl_line_with_expected_fields(self, tmp_path, monkeypatch):
        log_path = tmp_path / "ai_traces.jsonl"
        monkeypatch.setattr(observability, "TRACE_LOG_PATH", log_path)
        monkeypatch.setattr(observability, "_appinsights_ready", lambda: False)

        record = observability.record_ai_trace(
            workflow="resume_matching", stage="embedding_generation", status="succeeded",
            duration_ms=42.123, metadata={"model": "text-embedding-3-small"},
        )

        assert record["workflow"] == "resume_matching"
        assert record["stage"] == "embedding_generation"
        assert record["status"] == "succeeded"
        assert record["duration_ms"] == 42.1
        assert "trace_id" in record and record["trace_id"]
        assert "logged_at" in record

        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["trace_id"] == record["trace_id"]

    def test_golden_generates_a_trace_id_when_not_supplied(self, tmp_path, monkeypatch):
        monkeypatch.setattr(observability, "TRACE_LOG_PATH", tmp_path / "ai_traces.jsonl")
        monkeypatch.setattr(observability, "_appinsights_ready", lambda: False)
        r1 = observability.record_ai_trace(workflow="w", stage="s")
        r2 = observability.record_ai_trace(workflow="w", stage="s")
        assert r1["trace_id"] != r2["trace_id"]


class TestTraceStage:
    def test_golden_records_succeeded_on_normal_exit(self, tmp_path, monkeypatch):
        log_path = tmp_path / "ai_traces.jsonl"
        monkeypatch.setattr(observability, "TRACE_LOG_PATH", log_path)
        monkeypatch.setattr(observability, "_appinsights_ready", lambda: False)

        with observability.trace_stage("document_verification", "document_extraction") as trace_id:
            assert trace_id

        records = [json.loads(l) for l in log_path.read_text(encoding="utf-8").strip().splitlines()]
        assert len(records) == 1
        assert records[0]["status"] == "succeeded"
        assert records[0]["workflow"] == "document_verification"

    def test_adversarial_records_failed_and_still_reraises(self, tmp_path, monkeypatch):
        log_path = tmp_path / "ai_traces.jsonl"
        monkeypatch.setattr(observability, "TRACE_LOG_PATH", log_path)
        monkeypatch.setattr(observability, "_appinsights_ready", lambda: False)

        with pytest.raises(ValueError, match="boom"):
            with observability.trace_stage("document_verification", "document_extraction"):
                raise ValueError("boom")

        records = [json.loads(l) for l in log_path.read_text(encoding="utf-8").strip().splitlines()]
        assert records[0]["status"] == "failed"
        assert records[0]["error"] == "boom"


class TestRecordLlmCallAlsoEmitsTrace:
    def test_golden_llm_call_produces_both_a_metrics_record_and_a_trace(self, tmp_path, monkeypatch):
        monkeypatch.setattr(observability, "LOG_PATH", tmp_path / "llm_observability.jsonl")
        monkeypatch.setattr(observability, "TRACE_LOG_PATH", tmp_path / "ai_traces.jsonl")
        monkeypatch.setattr(observability, "_appinsights_ready", lambda: False)

        observability.record_llm_call(label="resume_chunk_summarization", model="gpt-3.5-turbo", total_tokens=120, cost_usd=0.001)

        llm_records = [json.loads(l) for l in observability.LOG_PATH.read_text(encoding="utf-8").strip().splitlines()]
        trace_records = [json.loads(l) for l in observability.TRACE_LOG_PATH.read_text(encoding="utf-8").strip().splitlines()]
        assert len(llm_records) == 1
        assert len(trace_records) == 1
        assert trace_records[0]["workflow"] == "llm"
        assert trace_records[0]["stage"] == "resume_chunk_summarization"
        assert trace_records[0]["status"] == "succeeded"

    def test_golden_workflow_and_trace_id_are_threaded_through_not_hardcoded(self, tmp_path, monkeypatch):
        """A span-group concept only works if every span sharing a
        trace_id also reports the SAME workflow -- this pins down that
        record_llm_call() no longer hardcodes workflow="llm" regardless
        of what trace it's asked to join (a real bug this feature's own
        design review caught: ResumeParser.py's LLM calls need to report
        workflow="resume_parsing" to match their sibling spans)."""
        monkeypatch.setattr(observability, "LOG_PATH", tmp_path / "llm_observability.jsonl")
        monkeypatch.setattr(observability, "TRACE_LOG_PATH", tmp_path / "ai_traces.jsonl")
        monkeypatch.setattr(observability, "_appinsights_ready", lambda: False)

        shared_trace_id = observability.new_trace_id()
        with observability.trace_stage("resume_parsing", "document_extraction", trace_id=shared_trace_id):
            pass
        observability.record_llm_call(
            label="resume_chunk_summarization", model="gpt-3.5-turbo", total_tokens=10,
            trace_id=shared_trace_id, workflow="resume_parsing",
        )

        trace_records = [json.loads(l) for l in observability.TRACE_LOG_PATH.read_text(encoding="utf-8").strip().splitlines()]
        assert len(trace_records) == 2
        assert {r["trace_id"] for r in trace_records} == {shared_trace_id}
        assert {r["workflow"] for r in trace_records} == {"resume_parsing"}
        assert len({r["span_id"] for r in trace_records}) == 2  # span_id always unique, even sharing a trace_id


class TestBoundMetadata:
    def test_golden_truncates_oversized_metadata_values(self, tmp_path, monkeypatch):
        monkeypatch.setattr(observability, "TRACE_LOG_PATH", tmp_path / "ai_traces.jsonl")
        monkeypatch.setattr(observability, "_appinsights_ready", lambda: False)
        huge_value = "x" * 5000
        record = observability.record_ai_trace(workflow="w", stage="s", metadata={"prompt": huge_value, "count": 3})
        assert len(record["metadata"]["prompt"]) < 500
        assert record["metadata"]["prompt"].endswith("...[truncated]")
        assert record["metadata"]["count"] == 3  # non-string values pass through untouched


# ---------------------------------------------------------------------------
# model_pricing.py
# ---------------------------------------------------------------------------
class TestModelPricing:
    def test_golden_estimates_known_embedding_model_cost(self):
        cost, source = model_pricing.estimate_embedding_cost("text-embedding-3-small", 1000)
        assert cost is not None and cost > 0
        assert source == "documented_estimate"

    def test_golden_unknown_model_returns_none_not_a_guess(self):
        cost, reason = model_pricing.estimate_embedding_cost("some-future-model-not-in-table", 1000)
        assert cost is None
        assert "No documented price" in reason

    def test_edge_missing_token_count_returns_none(self):
        cost, reason = model_pricing.estimate_embedding_cost("text-embedding-3-small", None)
        assert cost is None


# ---------------------------------------------------------------------------
# azure_integrations.py
# ---------------------------------------------------------------------------
class TestAzureIntegrationsRegistry:
    def test_golden_catalog_has_no_duplicate_ids(self):
        assert len(azure_integrations.ADAPTER_TYPE_IDS) == len(set(azure_integrations.ADAPTER_TYPE_IDS))

    def test_golden_only_cosmos_blob_and_key_vault_support_live_health_checks(self):
        # key_vault added in the Azure real-services audit pass --
        # secrets_loader.get_keyvault_status() does a real, free,
        # safe list_properties_of_secrets call.
        live = {k for k, v in azure_integrations.ADAPTER_TYPES.items() if v["capabilities"]["health_check"]}
        assert live == {"cosmos_db", "blob_storage", "key_vault"}

    def test_golden_only_cosmos_and_blob_declare_an_azure_resource_type(self):
        with_type = {k for k, v in azure_integrations.ADAPTER_TYPES.items() if v["azure_resource_type"]}
        assert with_type == {"cosmos_db", "blob_storage"}

    def test_golden_no_adapter_declares_any_resource_write_operation(self):
        """Pins the spec's explicit requirement: no adapter in this
        release implements a verified create/update/delete -- the
        resource_operations list must stay empty for all six."""
        for adapter in azure_integrations.ADAPTER_TYPES.values():
            assert adapter["capabilities"]["resource_operations"] == []


class TestCheckConnection:
    def test_golden_missing_secret_is_unknown_configuration_invalid_no_network_call(self, monkeypatch):
        monkeypatch.setattr(azure_integrations, "get_secret", lambda name: None)
        result = azure_integrations.check_connection("cosmos_db")
        assert result["outcome"] == "unknown"
        assert result["error_category"] == "configuration_invalid"
        assert "COSMOS_ENDPOINT" in result["detail"]

    def test_golden_configured_but_no_health_check_capability_is_honestly_unknown(self, monkeypatch):
        monkeypatch.setattr(azure_integrations, "get_secret", lambda name: "x")
        result = azure_integrations.check_connection("document_intelligence")
        assert result["outcome"] == "unknown"
        assert result["error_category"] is None

    def test_golden_live_check_success(self, monkeypatch):
        monkeypatch.setattr(azure_integrations, "get_secret", lambda name: "x")
        monkeypatch.setattr(azure_integrations, "_check_cosmos", lambda: None)
        result = azure_integrations.check_connection("cosmos_db")
        assert result["outcome"] == "healthy"

    def test_adversarial_live_check_failure_is_reported_and_redacted(self, monkeypatch):
        monkeypatch.setattr(azure_integrations, "get_secret", lambda name: "x")

        def broken():
            raise RuntimeError("connection failed: AccountKey=supersecretvalue1234567890")

        monkeypatch.setattr(azure_integrations, "_check_cosmos", broken)
        result = azure_integrations.check_connection("cosmos_db")
        assert result["outcome"] == "unhealthy"
        assert "supersecretvalue1234567890" not in result["detail"]
        assert "REDACTED" in result["detail"]

    def test_adversarial_unknown_adapter_type(self):
        result = azure_integrations.check_connection("not_a_real_adapter")
        assert result["outcome"] == "unknown"
        assert result["error_category"] == "configuration_invalid"


class TestDiscoverResources:
    def test_golden_unsupported_adapter_reports_not_supported_not_empty_list(self):
        result = azure_integrations.discover_resources("document_intelligence", "sub-1", "rg-1")
        assert result["supported"] is False
        assert result["resources"] == []
        assert "not supported" in result["error"]

    def test_golden_missing_scope_reports_not_supported(self):
        result = azure_integrations.discover_resources("cosmos_db", None, None)
        assert result["supported"] is False
        assert "required" in result["error"]

    def test_adversarial_a_failed_discovery_attempt_is_a_clear_error_not_an_empty_list_with_no_explanation(self):
        """When discovery IS attempted (adapter supports it, scope was
        supplied) but the underlying call fails -- missing SDK, no real
        credentials, denied permission, whatever -- `resources` must
        still be [] but `error` must say why. This is the spec's
        explicit warning against a bare empty list that falsely implies
        'zero resources exist'. In this sandbox there's no real Azure
        subscription, so this is always the failure path (not mocked --
        exercising the real code, same as this feature's own
        development-time verification)."""
        result = azure_integrations.discover_resources("cosmos_db", "sub-1", "rg-1")
        assert result["resources"] == []
        assert result["error"]  # never an empty-list-with-no-explanation


class TestRedactSecret:
    def test_golden_redacts_account_key(self):
        text = "AccountKey=abcDEF1234567890abcDEF1234567890xyz==;EndpointSuffix=core.windows.net"
        redacted = azure_integrations.redact_secret(text)
        assert "abcDEF1234567890abcDEF1234567890xyz" not in redacted
        assert "REDACTED" in redacted

    def test_golden_redacts_bearer_token(self):
        redacted = azure_integrations.redact_secret("Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig")
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in redacted

    def test_edge_empty_text_passthrough(self):
        assert azure_integrations.redact_secret("") == ""
        assert azure_integrations.redact_secret(None) is None


# ---------------------------------------------------------------------------
# Endpoint layer
# ---------------------------------------------------------------------------
_RECRUITER_ROLE_DOC = {"id": "recruiter", "role_key": "recruiter", "display_name": "Recruiter", "is_system": True}
_RECRUITER_USER = {"id": "u-rec", "username": "rec", "role_keys": ["recruiter"], "status": "active"}


def _req_with_params(params):
    return func.HttpRequest(
        method="GET", url="/api/test", body=None, params=params,
        headers={"Authorization": "Bearer " + _fixture_token()},
    )


class TestPercentile:
    def test_golden_insufficient_sample_returns_none(self):
        assert api._percentile([10, 20, 30], 50) is None  # fewer than _MIN_SAMPLE_FOR_PERCENTILE

    def test_golden_computes_real_percentile_with_enough_samples(self):
        values = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
        assert api._percentile(values, 50) is not None
        assert api._percentile(values, 95) >= api._percentile(values, 50)

    def test_golden_latency_stats_marks_insufficient_sample(self):
        stats = api._latency_stats([10, 20])
        assert stats["insufficient_sample"] is True
        assert stats["p50_ms"] is None
        assert stats["avg_ms"] == 15.0  # average is still real with any sample size


class TestGroupSpansIntoTraces:
    def test_golden_groups_by_trace_id_and_computes_overall_status(self):
        spans = [
            {"trace_id": "t1", "span_id": "s1", "workflow": "resume_parsing", "stage": "document_extraction",
             "status": "succeeded", "duration_ms": 100, "logged_at": "2026-01-01T00:00:00Z", "correlation_id": "cand-1"},
            {"trace_id": "t1", "span_id": "s2", "workflow": "resume_parsing", "stage": "chunking",
             "status": "failed", "duration_ms": 50, "logged_at": "2026-01-01T00:00:01Z", "correlation_id": "cand-1"},
        ]
        traces = api._group_spans_into_traces(spans)
        assert len(traces) == 1
        assert traces[0]["span_count"] == 2
        assert traces[0]["status"] == "failed"  # any failed span -> trace is failed
        assert traces[0]["correlation_id"] == "cand-1"
        assert traces[0]["total_duration_ms"] == 150.0
        assert traces[0]["stages"] == ["document_extraction", "chunking"]

    def test_edge_spans_with_no_trace_id_are_excluded(self):
        spans = [{"span_id": "s1", "workflow": "llm", "stage": "x", "status": "succeeded", "logged_at": "2026-01-01T00:00:00Z"}]
        assert api._group_spans_into_traces(spans) == []


class TestCostBreakdown:
    def test_golden_sums_only_priced_spans_by_workflow_and_model(self):
        records = [
            {"workflow": "llm", "metadata": {"cost_usd": 0.01, "model": "gpt-3.5-turbo"}},
            {"workflow": "resume_matching", "metadata": {"cost_usd": 0.002, "model": "text-embedding-3-small"}},
            {"workflow": "resume_parsing", "metadata": {}},  # no cost_usd -- excluded, not treated as $0
        ]
        breakdown = api._cost_breakdown(records)
        assert breakdown["priced_span_count"] == 2
        assert breakdown["total_cost_usd"] == 0.012
        assert breakdown["by_workflow"]["llm"] == 0.01
        assert breakdown["pricing_version"] == model_pricing.PRICING_VERSION


class TestInstrumentationCoverage:
    def test_golden_reports_covered_and_missing_documented_stages(self):
        coverage = api._instrumentation_coverage({"document_extraction", "chunking"})
        assert "document_extraction" in coverage["covered_stages"]
        assert "embedding_generation" in coverage["uninstrumented_stages"]
        assert coverage["coverage_pct"] is not None
        assert len(coverage["not_applicable_to_this_repo"]) > 0


class TestAiObservabilitySummaryEndpoint:
    def test_golden_empty_logs_return_real_zeros_not_fake_data(self, tmp_path, monkeypatch):
        monkeypatch.setattr(observability, "LOG_PATH", tmp_path / "llm_observability.jsonl")
        monkeypatch.setattr(observability, "TRACE_LOG_PATH", tmp_path / "ai_traces.jsonl")
        resp = api.ai_observability_summary_endpoint(_req())
        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["llm_calls"]["total_calls"] == 0
        assert body["ai_traces"]["total_spans"] == 0
        assert body["ai_traces"]["distinct_traces"] == 0
        assert body["requests"]["total"] == 0
        assert body["span_latency"]["insufficient_sample"] is True
        assert body["recent_traces"] == []

    def test_golden_aggregates_real_log_contents_into_traces(self, tmp_path, monkeypatch):
        llm_path = tmp_path / "llm_observability.jsonl"
        trace_path = tmp_path / "ai_traces.jsonl"
        llm_path.write_text(
            json.dumps({"label": "resume_chunk_summarization", "total_tokens": 100, "cost_usd": 0.01}) + "\n",
            encoding="utf-8",
        )
        trace_path.write_text(
            json.dumps({
                "trace_id": "t1", "span_id": "s1", "workflow": "resume_parsing", "stage": "document_extraction",
                "status": "succeeded", "duration_ms": 50.0, "logged_at": "2026-01-01T00:00:00Z",
                "correlation_id": "cand-1", "metadata": {},
            }) + "\n"
            + json.dumps({
                "trace_id": "t1", "span_id": "s2", "workflow": "resume_parsing", "stage": "resume_chunk_summarization",
                "status": "failed", "duration_ms": 10.0, "logged_at": "2026-01-02T00:00:00Z",
                "correlation_id": "cand-1", "metadata": {"cost_usd": 0.01, "model": "gpt-3.5-turbo"},
            }) + "\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(observability, "LOG_PATH", llm_path)
        monkeypatch.setattr(observability, "TRACE_LOG_PATH", trace_path)

        resp = api.ai_observability_summary_endpoint(_req())
        body = json.loads(resp.get_body())
        assert body["llm_calls"]["total_calls"] == 1
        assert body["ai_traces"]["total_spans"] == 2
        assert body["ai_traces"]["distinct_traces"] == 1
        assert body["requests"]["total"] == 1
        assert body["requests"]["failed"] == 1  # one span failed -> the whole trace counts as failed
        assert body["cost"]["total_cost_usd"] == 0.01
        # most recent (by started_at) first
        assert body["recent_traces"][0]["trace_id"] == "t1"

    def test_adversarial_recruiter_cannot_view_ai_observability(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_user", lambda uid: _RECRUITER_USER if uid == "u-rec" else None)
        monkeypatch.setattr(repositories, "get_role", lambda key: _RECRUITER_ROLE_DOC if key == "recruiter" else None)
        token = auth.create_token(_RECRUITER_USER)
        resp = api.ai_observability_summary_endpoint(func.HttpRequest(
            method="GET", url="/api/test", body=None, headers={"Authorization": f"Bearer {token}"},
        ))
        assert resp.status_code == 403

    def test_adversarial_missing_token_returns_401(self):
        resp = api.ai_observability_summary_endpoint(func.HttpRequest(method="GET", url="/api/test", body=None))
        assert resp.status_code == 401


_TWO_TRACE_SPANS = (
    json.dumps({
        "trace_id": "t1", "span_id": "s1", "workflow": "llm", "stage": "x", "status": "succeeded",
        "duration_ms": 10.0, "logged_at": "2026-01-01T00:00:00Z", "correlation_id": "cand-1", "metadata": {},
    }) + "\n"
    + json.dumps({
        "trace_id": "t2", "span_id": "s2", "workflow": "resume_matching", "stage": "embedding_generation",
        "status": "failed", "duration_ms": 20.0, "logged_at": "2026-01-02T00:00:00Z", "correlation_id": "cand-2",
        "metadata": {},
    }) + "\n"
)


class TestListAiTracesEndpoint:
    def test_golden_filters_by_workflow_and_status(self, tmp_path, monkeypatch):
        trace_path = tmp_path / "ai_traces.jsonl"
        trace_path.write_text(_TWO_TRACE_SPANS, encoding="utf-8")
        monkeypatch.setattr(observability, "TRACE_LOG_PATH", trace_path)

        resp = api.list_ai_traces_endpoint(_req())
        body = json.loads(resp.get_body())
        assert body["count"] == 2

        resp2 = api.list_ai_traces_endpoint(_req_with_params({"workflow": "resume_matching"}))
        body2 = json.loads(resp2.get_body())
        assert body2["count"] == 1
        assert body2["traces"][0]["workflow"] == "resume_matching"

        resp3 = api.list_ai_traces_endpoint(_req_with_params({"status": "failed"}))
        assert json.loads(resp3.get_body())["count"] == 1

    def test_golden_filters_by_correlation_id_substring(self, tmp_path, monkeypatch):
        trace_path = tmp_path / "ai_traces.jsonl"
        trace_path.write_text(_TWO_TRACE_SPANS, encoding="utf-8")
        monkeypatch.setattr(observability, "TRACE_LOG_PATH", trace_path)

        resp = api.list_ai_traces_endpoint(_req_with_params({"correlation_id": "cand-1"}))
        body = json.loads(resp.get_body())
        assert body["count"] == 1
        assert body["traces"][0]["trace_id"] == "t1"

    def test_golden_pagination(self, tmp_path, monkeypatch):
        trace_path = tmp_path / "ai_traces.jsonl"
        trace_path.write_text(_TWO_TRACE_SPANS, encoding="utf-8")
        monkeypatch.setattr(observability, "TRACE_LOG_PATH", trace_path)

        resp = api.list_ai_traces_endpoint(_req_with_params({"limit": "1", "offset": "0"}))
        body = json.loads(resp.get_body())
        assert body["count"] == 2  # total matching, independent of page size
        assert len(body["traces"]) == 1

    def test_adversarial_invalid_limit_returns_400(self):
        resp = api.list_ai_traces_endpoint(_req_with_params({"limit": "not-a-number"}))
        assert resp.status_code == 400

    def test_adversarial_invalid_sort_by_returns_400(self):
        resp = api.list_ai_traces_endpoint(_req_with_params({"sort_by": "not_a_real_field"}))
        assert resp.status_code == 400

    def test_adversarial_recruiter_cannot_list_traces(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_user", lambda uid: _RECRUITER_USER if uid == "u-rec" else None)
        monkeypatch.setattr(repositories, "get_role", lambda key: _RECRUITER_ROLE_DOC if key == "recruiter" else None)
        token = auth.create_token(_RECRUITER_USER)
        resp = api.list_ai_traces_endpoint(func.HttpRequest(
            method="GET", url="/api/test", body=None, headers={"Authorization": f"Bearer {token}"},
        ))
        assert resp.status_code == 403


class TestGetAiTraceEndpoint:
    def test_golden_returns_full_span_list_for_one_trace(self, tmp_path, monkeypatch):
        trace_path = tmp_path / "ai_traces.jsonl"
        trace_path.write_text(_TWO_TRACE_SPANS, encoding="utf-8")
        monkeypatch.setattr(observability, "TRACE_LOG_PATH", trace_path)

        resp = api.get_ai_trace_endpoint(_req(route_params={"trace_id": "t1"}))
        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["trace"]["trace_id"] == "t1"
        assert len(body["spans"]) == 1
        assert body["spans"][0]["span_id"] == "s1"

    def test_adversarial_unknown_trace_id_returns_404(self, tmp_path, monkeypatch):
        trace_path = tmp_path / "ai_traces.jsonl"
        trace_path.write_text(_TWO_TRACE_SPANS, encoding="utf-8")
        monkeypatch.setattr(observability, "TRACE_LOG_PATH", trace_path)
        resp = api.get_ai_trace_endpoint(_req(route_params={"trace_id": "does-not-exist"}))
        assert resp.status_code == 404

    def test_adversarial_observability_read_alone_is_not_enough(self, monkeypatch):
        """Trace detail requires the STRONGER observability.traces.read --
        a role holding only observability.read (aggregate view) must be
        refused, proving the two permission levels are actually distinct,
        not just two names for the same check."""
        aggregate_only_user = {"id": "u-agg", "username": "agg", "role_keys": ["aggregate_viewer"], "status": "active"}
        aggregate_only_role = {"id": "aggregate_viewer", "role_key": "aggregate_viewer", "permission_ids": ["observability.read"]}
        monkeypatch.setattr(repositories, "get_user", lambda uid: aggregate_only_user if uid == "u-agg" else None)
        monkeypatch.setattr(repositories, "get_role", lambda key: aggregate_only_role if key == "aggregate_viewer" else None)
        token = auth.create_token(aggregate_only_user)
        resp = api.get_ai_trace_endpoint(func.HttpRequest(
            method="GET", url="/api/test", route_params={"trace_id": "t1"}, body=None,
            headers={"Authorization": f"Bearer {token}"},
        ))
        assert resp.status_code == 403


_FIXTURE_INSTANCE = {
    "id": "cosmos_db", "adapter_type": "cosmos_db", "display_name": "Primary Cosmos DB",
    "environment": "production", "config": {}, "config_version": 1, "enabled": False,
    "last_connection_outcome": None, "last_health_checked_at": None,
    "created_by": "fixture_admin", "updated_by": None,
}


class TestListAzureAdaptersEndpoint:
    def test_golden_lists_the_six_known_adapter_types(self):
        resp = api.list_azure_adapters_endpoint(_req())
        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["count"] == len(azure_integrations.ADAPTER_TYPES)
        assert {a["adapter_type"] for a in body["adapters"]} == set(azure_integrations.ADAPTER_TYPE_IDS)


class TestListAzureIntegrationsEndpoint:
    def test_golden_reports_secret_presence_never_values(self, monkeypatch):
        monkeypatch.setattr(repositories, "list_integration_instances", lambda: [dict(_FIXTURE_INSTANCE)])
        monkeypatch.setattr(azure_integrations, "get_secret", lambda name: "super-secret-value" if name == "COSMOS_ENDPOINT" else None)
        resp = api.list_azure_integrations_endpoint(_req())
        assert resp.status_code == 200
        body_text = resp.get_body().decode()
        assert "super-secret-value" not in body_text
        body = json.loads(body_text)
        cosmos = next(i for i in body["integrations"] if i["integration_id"] == "cosmos_db")
        assert cosmos["configured"] is False  # COSMOS_KEY/COSMOS_DATABASE still missing

    def test_adversarial_recruiter_cannot_view_azure_services(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_user", lambda uid: _RECRUITER_USER if uid == "u-rec" else None)
        monkeypatch.setattr(repositories, "get_role", lambda key: _RECRUITER_ROLE_DOC if key == "recruiter" else None)
        token = auth.create_token(_RECRUITER_USER)
        resp = api.list_azure_integrations_endpoint(func.HttpRequest(
            method="GET", url="/api/test", body=None, headers={"Authorization": f"Bearer {token}"},
        ))
        assert resp.status_code == 403


class TestValidateAzureIntegrationConfigEndpoint:
    def test_golden_reports_missing_secrets(self, monkeypatch):
        monkeypatch.setattr(azure_integrations, "get_secret", lambda name: None)
        resp = api.validate_azure_integration_config_endpoint(_req(method="POST", body={"adapter_type": "cosmos_db"}))
        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["valid"] is False
        assert "COSMOS_ENDPOINT" in body["errors"][0]

    def test_adversarial_unknown_adapter_type_400(self):
        resp = api.validate_azure_integration_config_endpoint(_req(method="POST", body={"adapter_type": "not_real"}))
        assert resp.status_code == 400


class TestRegisterAzureIntegrationEndpoint:
    def test_golden_registers_disabled_by_default(self, monkeypatch):
        captured = []
        monkeypatch.setattr(repositories, "get_integration_instance", lambda iid: None)
        monkeypatch.setattr(repositories, "create_integration_instance", lambda *a, **k: dict(_FIXTURE_INSTANCE, id=a[0]))
        monkeypatch.setattr(repositories, "append_audit_log", lambda entry: captured.append(entry))
        resp = api.register_azure_integration_endpoint(_req(method="POST", body={
            "integration_id": "cosmos_db", "adapter_type": "cosmos_db",
            "display_name": "Primary Cosmos DB", "environment": "production",
        }))
        assert resp.status_code == 201
        assert json.loads(resp.get_body())["enabled"] is False
        assert any(e["action"] == "integration_registered" for e in captured)

    def test_adversarial_duplicate_id_409(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_integration_instance", lambda iid: dict(_FIXTURE_INSTANCE))
        resp = api.register_azure_integration_endpoint(_req(method="POST", body={
            "integration_id": "cosmos_db", "adapter_type": "cosmos_db",
            "display_name": "x", "environment": "production",
        }))
        assert resp.status_code == 409

    def test_adversarial_secret_field_in_config_is_rejected(self):
        resp = api.register_azure_integration_endpoint(_req(method="POST", body={
            "integration_id": "cosmos_db", "adapter_type": "cosmos_db", "display_name": "x",
            "environment": "production", "config": {"COSMOS_KEY": "sneaking-a-secret-in"},
        }))
        assert resp.status_code == 400

    def test_adversarial_recruiter_cannot_register(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_user", lambda uid: _RECRUITER_USER if uid == "u-rec" else None)
        monkeypatch.setattr(repositories, "get_role", lambda key: _RECRUITER_ROLE_DOC if key == "recruiter" else None)
        token = auth.create_token(_RECRUITER_USER)
        resp = api.register_azure_integration_endpoint(func.HttpRequest(
            method="POST", url="/api/test", body=json.dumps({"integration_id": "x", "adapter_type": "cosmos_db", "display_name": "x", "environment": "x"}).encode(),
            headers={"Authorization": f"Bearer {token}"},
        ))
        assert resp.status_code == 403


class TestUpdateAzureIntegrationConfigEndpoint:
    def test_golden_bumps_version_on_success(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_integration_instance", lambda iid: dict(_FIXTURE_INSTANCE))
        monkeypatch.setattr(repositories, "update_integration_instance", lambda doc: doc)
        monkeypatch.setattr(repositories, "record_integration_config_change", lambda *a, **k: None)
        monkeypatch.setattr(repositories, "append_audit_log", lambda entry: None)
        resp = api.update_azure_integration_config_endpoint(_req(
            method="PUT", route_params={"integration_id": "cosmos_db"},
            body={"config": {"subscription_id": "sub-1"}, "expected_version": 1},
        ))
        assert resp.status_code == 200
        assert json.loads(resp.get_body())["config_version"] == 2

    def test_adversarial_stale_version_409(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_integration_instance", lambda iid: dict(_FIXTURE_INSTANCE, config_version=3))
        resp = api.update_azure_integration_config_endpoint(_req(
            method="PUT", route_params={"integration_id": "cosmos_db"},
            body={"config": {"subscription_id": "sub-1"}, "expected_version": 1},
        ))
        assert resp.status_code == 409

    def test_adversarial_secret_field_rejected(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_integration_instance", lambda iid: dict(_FIXTURE_INSTANCE))
        resp = api.update_azure_integration_config_endpoint(_req(
            method="PUT", route_params={"integration_id": "cosmos_db"},
            body={"config": {"COSMOS_KEY": "nope"}, "expected_version": 1},
        ))
        assert resp.status_code == 400


class TestSetAzureIntegrationEnabledEndpoint:
    def test_golden_enable_requires_a_prior_healthy_check(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_integration_instance", lambda iid: dict(_FIXTURE_INSTANCE, last_connection_outcome=None))
        resp = api.set_azure_integration_enabled_endpoint(_req(
            method="PUT", route_params={"integration_id": "cosmos_db"}, body={"enabled": True},
        ))
        assert resp.status_code == 409

    def test_golden_enable_succeeds_after_a_healthy_check(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_integration_instance", lambda iid: dict(_FIXTURE_INSTANCE, last_connection_outcome="healthy"))
        monkeypatch.setattr(repositories, "update_integration_instance", lambda doc: doc)
        monkeypatch.setattr(repositories, "record_integration_config_change", lambda *a, **k: None)
        monkeypatch.setattr(repositories, "append_audit_log", lambda entry: None)
        resp = api.set_azure_integration_enabled_endpoint(_req(
            method="PUT", route_params={"integration_id": "cosmos_db"}, body={"enabled": True},
        ))
        assert resp.status_code == 200
        assert json.loads(resp.get_body())["enabled"] is True

    def test_golden_disable_always_allowed(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_integration_instance", lambda iid: dict(_FIXTURE_INSTANCE, enabled=True, last_connection_outcome=None))
        monkeypatch.setattr(repositories, "update_integration_instance", lambda doc: doc)
        monkeypatch.setattr(repositories, "record_integration_config_change", lambda *a, **k: None)
        monkeypatch.setattr(repositories, "append_audit_log", lambda entry: None)
        resp = api.set_azure_integration_enabled_endpoint(_req(
            method="PUT", route_params={"integration_id": "cosmos_db"}, body={"enabled": False},
        ))
        assert resp.status_code == 200


class TestCheckAzureIntegrationEndpoint:
    def setup_method(self):
        api._azure_check_last_run.clear()

    def test_golden_runs_check_persists_history_and_audits(self, monkeypatch):
        captured = []
        monkeypatch.setattr(repositories, "get_integration_instance", lambda iid: dict(_FIXTURE_INSTANCE))
        monkeypatch.setattr(azure_integrations, "check_connection", lambda adapter_type: {"adapter_type": adapter_type, "outcome": "unknown", "error_category": "configuration_invalid", "detail": "x", "duration_ms": 1.0, "checked_at": "now"})
        monkeypatch.setattr(repositories, "record_integration_health_check", lambda iid, result: dict(result, integration_id=iid, id="hc-1"))
        monkeypatch.setattr(repositories, "append_audit_log", lambda entry: captured.append(entry))
        resp = api.check_azure_integration_endpoint(_req(method="POST", route_params={"integration_id": "cosmos_db"}))
        assert resp.status_code == 200
        assert any(e["action"] == "integration_connection_checked" and e["integration_id"] == "cosmos_db" for e in captured)

    def test_adversarial_unknown_integration_404(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_integration_instance", lambda iid: None)
        resp = api.check_azure_integration_endpoint(_req(method="POST", route_params={"integration_id": "not_real"}))
        assert resp.status_code == 404

    def test_adversarial_rate_limited_second_call(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_integration_instance", lambda iid: dict(_FIXTURE_INSTANCE))
        monkeypatch.setattr(azure_integrations, "check_connection", lambda adapter_type: {"adapter_type": adapter_type, "outcome": "unknown", "error_category": "configuration_invalid", "detail": "x", "duration_ms": 1.0, "checked_at": "now"})
        monkeypatch.setattr(repositories, "record_integration_health_check", lambda iid, result: dict(result, integration_id=iid, id="hc-1"))
        monkeypatch.setattr(repositories, "append_audit_log", lambda entry: None)
        resp1 = api.check_azure_integration_endpoint(_req(method="POST", route_params={"integration_id": "cosmos_db"}))
        assert resp1.status_code == 200
        resp2 = api.check_azure_integration_endpoint(_req(method="POST", route_params={"integration_id": "cosmos_db"}))
        assert resp2.status_code == 429

    def test_adversarial_recruiter_cannot_trigger_a_check(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_user", lambda uid: _RECRUITER_USER if uid == "u-rec" else None)
        monkeypatch.setattr(repositories, "get_role", lambda key: _RECRUITER_ROLE_DOC if key == "recruiter" else None)
        token = auth.create_token(_RECRUITER_USER)
        resp = api.check_azure_integration_endpoint(func.HttpRequest(
            method="POST", url="/api/test", route_params={"integration_id": "cosmos_db"}, body=None,
            headers={"Authorization": f"Bearer {token}"},
        ))
        assert resp.status_code == 403


class TestGetAzureIntegrationHealthHistoryEndpoint:
    def test_golden_returns_history_for_existing_instance(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_integration_instance", lambda iid: dict(_FIXTURE_INSTANCE))
        monkeypatch.setattr(repositories, "list_integration_health_checks", lambda iid, limit=50: [{"outcome": "healthy"}])
        resp = api.get_azure_integration_health_history_endpoint(_req(route_params={"integration_id": "cosmos_db"}))
        assert resp.status_code == 200
        assert json.loads(resp.get_body())["count"] == 1

    def test_adversarial_unknown_integration_404(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_integration_instance", lambda iid: None)
        resp = api.get_azure_integration_health_history_endpoint(_req(route_params={"integration_id": "not_real"}))
        assert resp.status_code == 404


class TestDiscoverAzureResourcesEndpoint:
    def test_golden_reports_not_supported_without_fabricating_a_list(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_integration_instance", lambda iid: dict(_FIXTURE_INSTANCE, adapter_type="document_intelligence", config={}))
        monkeypatch.setattr(repositories, "append_audit_log", lambda entry: None)
        resp = api.discover_azure_resources_endpoint(_req(method="POST", route_params={"integration_id": "cosmos_db"}))
        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["supported"] is False
        assert body["resources"] == []

    def test_adversarial_unknown_integration_404(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_integration_instance", lambda iid: None)
        resp = api.discover_azure_resources_endpoint(_req(method="POST", route_params={"integration_id": "not_real"}))
        assert resp.status_code == 404


class TestRemoveAzureIntegrationEndpoint:
    def test_golden_removes_registration_only_never_touches_the_resource(self, monkeypatch):
        captured = []
        deleted = []
        monkeypatch.setattr(repositories, "get_integration_instance", lambda iid: dict(_FIXTURE_INSTANCE))
        monkeypatch.setattr(repositories, "get_container", lambda name: type("C", (), {"delete_item": staticmethod(lambda item, partition_key: deleted.append(item))})())
        monkeypatch.setattr(repositories, "record_integration_config_change", lambda *a, **k: None)
        monkeypatch.setattr(repositories, "append_audit_log", lambda entry: captured.append(entry))
        resp = api.remove_azure_integration_endpoint(_req(method="DELETE", route_params={"integration_id": "cosmos_db"}))
        assert resp.status_code == 200
        assert deleted == ["cosmos_db"]
        note = next(e["note"] for e in captured if e["action"] == "integration_registration_removed")
        assert "not touched" in note

    def test_adversarial_unknown_integration_404(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_integration_instance", lambda iid: None)
        resp = api.remove_azure_integration_endpoint(_req(method="DELETE", route_params={"integration_id": "not_real"}))
        assert resp.status_code == 404

    def test_adversarial_recruiter_cannot_remove(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_user", lambda uid: _RECRUITER_USER if uid == "u-rec" else None)
        monkeypatch.setattr(repositories, "get_role", lambda key: _RECRUITER_ROLE_DOC if key == "recruiter" else None)
        token = auth.create_token(_RECRUITER_USER)
        resp = api.remove_azure_integration_endpoint(func.HttpRequest(
            method="DELETE", url="/api/test", route_params={"integration_id": "cosmos_db"}, body=None,
            headers={"Authorization": f"Bearer {token}"},
        ))
        assert resp.status_code == 403


class TestGetAzureIntegrationEndpoint:
    def test_golden_returns_instance_adapter_and_recent_history(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_integration_instance", lambda iid: dict(_FIXTURE_INSTANCE))
        monkeypatch.setattr(repositories, "list_integration_health_checks", lambda iid, limit=10: [])
        resp = api.get_azure_integration_endpoint(_req(route_params={"integration_id": "cosmos_db"}))
        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["instance"]["id"] == "cosmos_db"
        assert body["adapter"]["adapter_type"] == "cosmos_db"
        assert "secrets_configured" in body

    def test_adversarial_unknown_integration_404(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_integration_instance", lambda iid: None)
        resp = api.get_azure_integration_endpoint(_req(route_params={"integration_id": "not_real"}))
        assert resp.status_code == 404

    def test_adversarial_azure_read_alone_is_not_enough(self, monkeypatch):
        """Instance detail (config/diagnostics) requires the STRONGER
        azure.config.read -- azure.read (inventory only) must be refused."""
        inventory_only_user = {"id": "u-inv", "username": "inv", "role_keys": ["inventory_viewer"], "status": "active"}
        inventory_only_role = {"id": "inventory_viewer", "role_key": "inventory_viewer", "permission_ids": ["azure.read"]}
        monkeypatch.setattr(repositories, "get_user", lambda uid: inventory_only_user if uid == "u-inv" else None)
        monkeypatch.setattr(repositories, "get_role", lambda key: inventory_only_role if key == "inventory_viewer" else None)
        token = auth.create_token(inventory_only_user)
        resp = api.get_azure_integration_endpoint(func.HttpRequest(
            method="GET", url="/api/test", route_params={"integration_id": "cosmos_db"}, body=None,
            headers={"Authorization": f"Bearer {token}"},
        ))
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Planned (not-implemented) adapters -- azure_ai_foundry/azure_ai_search.
# This repo genuinely has no code path for either (verified against
# semantic-library/embeddings.py and vector_store.py's own docstrings),
# so the registry must label them "planned", never silently omit them or
# let them be registered/validated/checked as if they were real.
# ---------------------------------------------------------------------------
class TestPlannedAdapters:
    """azure_ai_foundry moved from "planned" to "supported" in the Azure
    real-services audit pass (llm_utils.get_chat_llm()/
    get_embedding_client_and_model() now really route to Azure OpenAI
    once configured -- see azure_integrations.py's adapter entry).
    azure_ai_search remains the one genuinely planned-but-not-implemented
    adapter (semantic-library/vector_store.py's LocalVectorStore is
    still local JSON+numpy, confirmed unchanged)."""

    def test_golden_only_search_is_marked_planned(self):
        assert azure_integrations.get_adapter("azure_ai_search")["status"] == "planned"
        # azure_speech was removed from the adapter registry (not from
        # voicebot/azure_speech_utils.py itself, which still works) --
        # the user explicitly asked for it dropped from the Azure Service
        # Hub / Readiness / Observability Report service lists, since it
        # isn't a service CDPI needs configured right now.
        for key in ("cosmos_db", "blob_storage", "document_intelligence", "key_vault", "application_insights", "azure_ai_foundry"):
            assert azure_integrations.get_adapter(key)["status"] == "supported"
        assert azure_integrations.get_adapter("azure_speech") is None

    def test_golden_planned_adapter_has_no_capabilities(self):
        caps = azure_integrations.get_adapter("azure_ai_search")["capabilities"]
        assert caps == {"health_check": False, "resource_discovery": False, "resource_operations": []}

    def test_golden_azure_ai_foundry_has_no_safe_free_health_check(self):
        # Real now, but still health_check: False -- verifying it means a
        # real billable chat/embedding call, which no automatic scan
        # ever makes (see llm_utils.run_azure_openai_test_request(),
        # explicit-only).
        caps = azure_integrations.get_adapter("azure_ai_foundry")["capabilities"]
        assert caps["health_check"] is False

    def test_golden_validate_config_refuses_a_planned_adapter(self):
        errors = azure_integrations.validate_config("azure_ai_search")
        assert errors and "planned" in errors[0]

    def test_golden_check_connection_reports_planned_not_a_fake_status(self):
        result = azure_integrations.check_connection("azure_ai_search")
        assert result["outcome"] == "unknown"
        assert result["error_category"] == "configuration_invalid"
        assert "planned" in result["detail"]

    def test_adversarial_default_seed_never_creates_an_instance_for_a_planned_adapter(self, monkeypatch):
        """ensure_default_integration_instances() seeds one ALREADY-IN-USE
        instance per real adapter -- it must never do that for a planned
        adapter, since nothing is genuinely in use yet. Exercises the
        real function (not a copy) against a fake container."""
        created_docs = {}

        class FakeContainer:
            def upsert_item(self, doc):
                created_docs[doc["id"]] = doc
                return doc

            def read_item(self, item, partition_key):
                from azure.cosmos.exceptions import CosmosResourceNotFoundError
                if item in created_docs:
                    return created_docs[item]
                raise CosmosResourceNotFoundError(message="not found")

        monkeypatch.setattr(repositories, "get_container", lambda name: FakeContainer())
        repositories.ensure_default_integration_instances()
        assert "azure_ai_search" not in created_docs
        assert "cosmos_db" in created_docs

    def test_adversarial_cannot_register_a_planned_adapter(self):
        resp = api.register_azure_integration_endpoint(_req(method="POST", body={
            "integration_id": "search_prod", "adapter_type": "azure_ai_search",
            "display_name": "x", "environment": "production",
        }))
        assert resp.status_code == 400
        assert "planned" in json.loads(resp.get_body())["error"]


# ---------------------------------------------------------------------------
# Configuration change history + rollback -- distinct from health-check
# history (TestGetAzureIntegrationHealthHistoryEndpoint above): this is
# "what config/lifecycle changes happened," not "did a live connection
# succeed."
# ---------------------------------------------------------------------------
class TestConfigHistoryAndRollback:
    def test_golden_config_update_resets_validation_and_disables(self, monkeypatch):
        """A config change invalidates any prior 'healthy' verdict and
        drops an already-enabled integration back to draft -- the spec's
        'revalidate before activation' rule, enforced structurally."""
        saved = {}
        monkeypatch.setattr(repositories, "get_integration_instance", lambda iid: dict(_FIXTURE_INSTANCE, enabled=True, last_connection_outcome="healthy", lifecycle_status="active"))
        monkeypatch.setattr(repositories, "update_integration_instance", lambda doc: saved.update(doc) or doc)
        monkeypatch.setattr(repositories, "record_integration_config_change", lambda *a, **k: None)
        monkeypatch.setattr(repositories, "append_audit_log", lambda entry: None)
        resp = api.update_azure_integration_config_endpoint(_req(
            method="PUT", route_params={"integration_id": "cosmos_db"},
            body={"config": {"subscription_id": "sub-1"}, "expected_version": 1},
        ))
        assert resp.status_code == 200
        assert saved["enabled"] is False
        assert saved["lifecycle_status"] == "draft"
        assert saved["last_connection_outcome"] is None

    def test_golden_get_history_returns_entries(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_integration_instance", lambda iid: dict(_FIXTURE_INSTANCE))
        monkeypatch.setattr(repositories, "list_integration_config_history", lambda iid, limit=50: [{"id": "h1", "operation": "registered"}])
        resp = api.get_azure_integration_config_history_endpoint(_req(route_params={"integration_id": "cosmos_db"}))
        assert resp.status_code == 200
        assert json.loads(resp.get_body())["count"] == 1

    def test_adversarial_get_history_unknown_integration_404(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_integration_instance", lambda iid: None)
        resp = api.get_azure_integration_config_history_endpoint(_req(route_params={"integration_id": "not_real"}))
        assert resp.status_code == 404

    def test_golden_rollback_restores_a_prior_config_snapshot_as_a_new_version(self, monkeypatch):
        saved = {}
        history = [{"id": "h2", "operation": "config_updated", "safe_summary": {"config": {"subscription_id": "new"}, "config_version": 2}},
                   {"id": "h1", "operation": "registered", "safe_summary": {"config": {"subscription_id": "old"}, "config_version": 1}}]
        monkeypatch.setattr(repositories, "get_integration_instance", lambda iid: dict(_FIXTURE_INSTANCE, config_version=2, enabled=True))
        monkeypatch.setattr(repositories, "list_integration_config_history", lambda iid, limit=200: history)
        monkeypatch.setattr(repositories, "update_integration_instance", lambda doc: saved.update(doc) or doc)
        monkeypatch.setattr(repositories, "record_integration_config_change", lambda *a, **k: None)
        monkeypatch.setattr(repositories, "append_audit_log", lambda entry: None)
        resp = api.rollback_azure_integration_config_endpoint(_req(
            method="POST", route_params={"integration_id": "cosmos_db", "history_id": "h1"},
        ))
        assert resp.status_code == 200
        assert saved["config"] == {"subscription_id": "old"}
        assert saved["config_version"] == 3  # a NEW version, history untouched
        assert saved["enabled"] is False  # forced back to draft, must be revalidated

    def test_adversarial_rollback_unknown_history_entry_404(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_integration_instance", lambda iid: dict(_FIXTURE_INSTANCE))
        monkeypatch.setattr(repositories, "list_integration_config_history", lambda iid, limit=200: [])
        resp = api.rollback_azure_integration_config_endpoint(_req(
            method="POST", route_params={"integration_id": "cosmos_db", "history_id": "does-not-exist"},
        ))
        assert resp.status_code == 404

    def test_adversarial_rollback_to_a_non_config_entry_is_refused(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_integration_instance", lambda iid: dict(_FIXTURE_INSTANCE))
        monkeypatch.setattr(repositories, "list_integration_config_history", lambda iid, limit=200: [{"id": "h1", "operation": "activated", "safe_summary": {"config_version": 1}}])
        resp = api.rollback_azure_integration_config_endpoint(_req(
            method="POST", route_params={"integration_id": "cosmos_db", "history_id": "h1"},
        ))
        assert resp.status_code == 400

    def test_adversarial_recruiter_cannot_rollback(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_user", lambda uid: _RECRUITER_USER if uid == "u-rec" else None)
        monkeypatch.setattr(repositories, "get_role", lambda key: _RECRUITER_ROLE_DOC if key == "recruiter" else None)
        token = auth.create_token(_RECRUITER_USER)
        resp = api.rollback_azure_integration_config_endpoint(func.HttpRequest(
            method="POST", url="/api/test", route_params={"integration_id": "cosmos_db", "history_id": "h1"}, body=None,
            headers={"Authorization": f"Bearer {token}"},
        ))
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Page/permission catalog sanity for the two new pages.
# ---------------------------------------------------------------------------
class TestNewPagesAndPermissions:
    def test_golden_new_permissions_exist_and_admin_has_them(self):
        admin = next(r for r in auth.DEFAULT_ROLES if r["role_key"] == "admin")
        for pid in ("observability.read", "azure.read", "azure.manage"):
            assert pid in auth.PERMISSIONS
            assert pid in admin["permission_ids"]

    def test_golden_new_pages_registered_with_correct_required_permission(self):
        ai_obs_page = next(p for p in auth.PAGES if p["page_id"] == "ai_observability")
        azure_page = next(p for p in auth.PAGES if p["page_id"] == "azure_services")
        assert ai_obs_page["required_permission"] == "observability.read"
        assert azure_page["required_permission"] == "azure.read"

    def test_golden_recruiter_and_candidate_get_neither_new_page(self):
        # ai_operations_mlops/azure_cloud_administrator legitimately hold
        # azure.*, and recruiter/recruitment_ops_manager/ai_operations_mlops/
        # azure_cloud_administrator hold observability.read -- the first two
        # for their required Resume Processing Report (CDPI Product Personas
        # doc section 8.4), azure_cloud_administrator for the Service
        # Observability Report page (see auth.py's "azure_observability_report"
        # PAGES entry and docs/AZURE_OBSERVABILITY.md), which the spec that
        # introduced it explicitly scoped to platform administrators, Azure/
        # cloud administrators, and AI operations/MLOps users -- checked
        # per-permission so no role is wrongly exempted from a check it
        # doesn't actually need.
        observability_exempt = {"admin", "ai_operations_mlops", "recruiter", "recruitment_ops_manager", "azure_cloud_administrator"}
        azure_exempt = {"admin", "azure_cloud_administrator"}
        for role in auth.DEFAULT_ROLES:
            if role["role_key"] not in observability_exempt:
                assert "observability.read" not in role["permission_ids"]
            if role["role_key"] not in azure_exempt:
                assert "azure.read" not in role["permission_ids"]
                assert "azure.manage" not in role["permission_ids"]
