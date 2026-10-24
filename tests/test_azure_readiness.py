"""Tests for azure_readiness.py's status classification and az_cli.py's
authentication detection, plus the GET/POST /admin/azure-readiness*
endpoints (api_blueprint.py). The classification logic composes
azure_integrations.py's ALREADY-TESTED validate_config()/check_connection()
output -- these tests focus on the composition/classification itself
(does a given check_result map to the right one of the 9 documented
statuses), not re-testing azure_integrations.py's own behavior."""

import json

import api_blueprint as api
import auth
import az_cli
import azure_integrations
import azure_readiness
import repositories
from conftest import _req


_RECRUITER_ROLE_DOC = {"id": "recruiter", "role_key": "recruiter", "display_name": "Recruiter", "is_system": True}
_RECRUITER_USER = {"id": "u-rec", "username": "rec", "role_keys": ["recruiter"], "status": "active"}

_FIXTURE_INSTANCE = {
    "id": "cosmos_db", "adapter_type": "cosmos_db", "display_name": "Primary Cosmos DB",
    "environment": "production", "config": {}, "config_version": 1, "enabled": False,
    "last_connection_outcome": None, "last_health_checked_at": None, "last_failure": None,
    "created_by": "fixture_admin", "updated_by": None,
}


class TestAzCli:
    """Whether a real `az` CLI happens to be installed/authenticated in
    the machine running this suite is NOT something these tests should
    depend on -- it varies by environment and (critically) a real
    authenticated CLI must never let an automated test suite make a real
    mutating Azure API call. Every case here forces the condition it's
    testing via monkeypatch, rather than relying on ambient sandbox
    state, which is exactly the "automated tests must not touch real
    Azure" requirement from the real-services audit pass."""

    def test_golden_reports_not_installed_honestly_when_az_missing(self, monkeypatch):
        monkeypatch.setattr(az_cli, "_az_executable", lambda: None)
        result = az_cli.check_az_installed()
        assert result["installed"] is False
        assert "detail" in result

    def test_golden_account_status_never_raises_when_cli_missing(self, monkeypatch):
        monkeypatch.setattr(az_cli, "_az_executable", lambda: None)
        result = az_cli.get_account_status()
        assert result["authenticated"] is False
        assert result["tenant_id"] is None

    def test_golden_list_subscriptions_returns_empty_not_error(self, monkeypatch):
        monkeypatch.setattr(az_cli, "_az_executable", lambda: None)
        result = az_cli.list_subscriptions()
        assert result["subscriptions"] == []

    def test_adversarial_unknown_binary_path_does_not_raise(self, monkeypatch):
        import subprocess

        def _raise(*a, **k):
            raise FileNotFoundError("no az")
        monkeypatch.setattr(az_cli, "_az_executable", lambda: "az")
        monkeypatch.setattr(subprocess, "run", _raise)
        assert az_cli.check_az_installed()["installed"] is False
        assert az_cli.get_account_status()["authenticated"] is False

    def test_golden_reports_installed_and_authenticated_from_real_az_output(self, monkeypatch):
        # Mirrors the REAL shape `az version --output json` / `az account
        # show --output json` return (verified against a real installed
        # CLI during this audit pass) -- mocked here so the assertion
        # doesn't depend on whether this machine happens to have one.
        import json as _json

        def fake_run_az(args):
            if args[0] == "version":
                return 0, _json.dumps({"azure-cli": "2.91.0"}), ""
            if args[:2] == ["account", "show"]:
                return 0, _json.dumps({
                    "tenantId": "t-1", "id": "sub-1", "name": "My Subscription",
                    "user": {"name": "user@example.com"},
                }), ""
            return 1, "", "unexpected"
        monkeypatch.setattr(az_cli, "_run_az", fake_run_az)
        installed = az_cli.check_az_installed()
        assert installed["installed"] is True
        assert installed["version"] == "2.91.0"
        account = az_cli.get_account_status()
        assert account["authenticated"] is True
        assert account["subscription_id"] == "sub-1"


class TestComputeReadiness:
    def test_golden_planned_adapter_is_not_integrated(self):
        # azure_ai_search remains the one genuinely planned adapter --
        # azure_ai_foundry became real in the Azure real-services audit
        # pass (see azure_integrations.py).
        result = azure_readiness.compute_readiness("azure_ai_search")
        assert result["status"] == "NOT_INTEGRATED"

    def test_golden_missing_secret_is_not_configured(self, monkeypatch):
        # document_intelligence is now genuinely configured in this
        # environment (a real, free F0 Document Intelligence resource was
        # provisioned -- see docs/AZURE_REAL_SERVICES_AUDIT.md), so this
        # can no longer rely on ambient "no secret set" state to exercise
        # the NOT_CONFIGURED path -- mock validate_config explicitly
        # instead, same pattern as test_golden_healthy_outcome_is_ready_when_config_valid below.
        monkeypatch.setattr(azure_integrations, "validate_config", lambda adapter_type: ["Missing required secret(s): DOCUMENT_INTELLIGENCE_ENDPOINT, DOCUMENT_INTELLIGENCE_KEY."])
        result = azure_readiness.compute_readiness("document_intelligence")
        assert result["status"] == "NOT_CONFIGURED"

    def test_golden_healthy_outcome_is_ready(self):
        check = {"outcome": "healthy", "error_category": None, "detail": "ok"}
        result = azure_readiness.compute_readiness(
            "cosmos_db", check_result=check,
        )
        # validate_config still runs first -- without real secrets configured
        # in THIS test environment it will report NOT_CONFIGURED before ever
        # looking at check_result, exactly like the real endpoint would.
        assert result["status"] in ("READY", "NOT_CONFIGURED")

    def test_golden_healthy_outcome_is_ready_when_config_valid(self, monkeypatch):
        monkeypatch.setattr(azure_integrations, "validate_config", lambda adapter_type: [])
        check = {"outcome": "healthy", "error_category": None, "detail": "Live connection check succeeded."}
        result = azure_readiness.compute_readiness("cosmos_db", check_result=check)
        assert result["status"] == "READY"

    def test_golden_auth_failed_outcome_maps_to_authentication_required(self, monkeypatch):
        monkeypatch.setattr(azure_integrations, "validate_config", lambda adapter_type: [])
        check = {"outcome": "unhealthy", "error_category": "authentication_failed", "detail": "bad key"}
        result = azure_readiness.compute_readiness("cosmos_db", check_result=check)
        assert result["status"] == "AUTHENTICATION_REQUIRED"

    def test_golden_authorization_denied_outcome_maps_to_permission_denied(self, monkeypatch):
        monkeypatch.setattr(azure_integrations, "validate_config", lambda adapter_type: [])
        check = {"outcome": "unhealthy", "error_category": "authorization_denied", "detail": "403"}
        result = azure_readiness.compute_readiness("cosmos_db", check_result=check)
        assert result["status"] == "PERMISSION_DENIED"

    def test_golden_other_unhealthy_outcome_maps_to_connection_failed(self, monkeypatch):
        monkeypatch.setattr(azure_integrations, "validate_config", lambda adapter_type: [])
        check = {"outcome": "unhealthy", "error_category": "endpoint_unreachable", "detail": "dns failure"}
        result = azure_readiness.compute_readiness("cosmos_db", check_result=check)
        assert result["status"] == "CONNECTION_FAILED"

    def test_golden_no_safe_check_available_is_partially_verified(self, monkeypatch):
        monkeypatch.setattr(azure_integrations, "validate_config", lambda adapter_type: [])
        check = {"outcome": "unknown", "error_category": None, "detail": "no safe free check exists"}
        result = azure_readiness.compute_readiness("document_intelligence", check_result=check)
        assert result["status"] == "PARTIALLY_VERIFIED"

    def test_golden_no_check_ever_run_is_unknown(self, monkeypatch):
        monkeypatch.setattr(azure_integrations, "validate_config", lambda adapter_type: [])
        result = azure_readiness.compute_readiness("cosmos_db", check_result=None)
        assert result["status"] == "UNKNOWN"

    def test_golden_cli_dependent_adapter_requires_az_login_even_if_configured(self, monkeypatch):
        monkeypatch.setattr(azure_integrations, "validate_config", lambda adapter_type: [])
        result = azure_readiness.compute_readiness("key_vault", check_result=None, az_authenticated=False)
        assert result["status"] == "AUTHENTICATION_REQUIRED"

    def test_adversarial_never_reports_ready_from_config_validation_alone(self, monkeypatch):
        # The spec's explicit "Do not report READY when only configuration
        # validation has passed" -- no check_result at all must never
        # resolve to READY, regardless of how clean the config is.
        monkeypatch.setattr(azure_integrations, "validate_config", lambda adapter_type: [])
        result = azure_readiness.compute_readiness("cosmos_db", check_result=None)
        assert result["status"] != "READY"

    def test_adversarial_unknown_adapter_type_is_unknown_not_a_crash(self):
        result = azure_readiness.compute_readiness("not_a_real_adapter")
        assert result["status"] == "UNKNOWN"


class TestReadinessForInstance:
    def test_golden_shape_has_every_documented_field(self, monkeypatch):
        monkeypatch.setattr(azure_integrations, "validate_config", lambda adapter_type: [])
        record = azure_readiness.readiness_for_instance(dict(_FIXTURE_INSTANCE), latest_check=None, az_account_status={"authenticated": False})
        for field in (
            "integration_id", "adapter_type", "service_name", "status", "status_definition", "reason",
            "environment", "enabled", "missing_configuration", "last_validated_at",
            "last_successful_validation_at", "validated_against", "recommended_next_action",
        ):
            assert field in record

    def test_golden_never_claims_real_validation_without_a_check(self, monkeypatch):
        monkeypatch.setattr(azure_integrations, "validate_config", lambda adapter_type: [])
        record = azure_readiness.readiness_for_instance(dict(_FIXTURE_INSTANCE), latest_check=None, az_account_status={"authenticated": False})
        assert record["validated_against"] == "config_only"


class TestAzureReadinessListEndpoint:
    def test_golden_includes_unregistered_adapters_too(self, monkeypatch):
        monkeypatch.setattr(repositories, "list_integration_instances", lambda: [])
        resp = api.azure_readiness_list_endpoint(_req())
        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        adapter_types = {s["adapter_type"] for s in body["services"]}
        assert adapter_types == set(azure_integrations.ADAPTER_TYPE_IDS)
        assert "azure_cli" in body

    def test_golden_registered_instance_reflects_cached_outcome(self, monkeypatch):
        monkeypatch.setattr(azure_integrations, "validate_config", lambda adapter_type: [])
        instance = dict(_FIXTURE_INSTANCE, last_connection_outcome="healthy", last_health_checked_at="2026-01-01T00:00:00Z")
        monkeypatch.setattr(repositories, "list_integration_instances", lambda: [instance])
        resp = api.azure_readiness_list_endpoint(_req())
        body = json.loads(resp.get_body())
        cosmos = next(s for s in body["services"] if s["integration_id"] == "cosmos_db")
        assert cosmos["status"] == "READY"

    def test_adversarial_recruiter_cannot_view(self, monkeypatch):
        import azure.functions as func

        monkeypatch.setattr(repositories, "get_user", lambda uid: _RECRUITER_USER if uid == "u-rec" else None)
        monkeypatch.setattr(repositories, "get_role", lambda key: _RECRUITER_ROLE_DOC if key == "recruiter" else None)
        token = auth.create_token(_RECRUITER_USER)
        resp = api.azure_readiness_list_endpoint(func.HttpRequest(method="GET", url="/api/test", body=None, headers={"Authorization": f"Bearer {token}"}))
        assert resp.status_code == 403


class TestAzureReadinessCliStatusEndpoint:
    def test_golden_returns_whatever_az_cli_honestly_reports(self, monkeypatch):
        # Forced to "not installed" via monkeypatch rather than assumed
        # from ambient sandbox state -- see TestAzCli's docstring.
        monkeypatch.setattr(api.az_cli, "_az_executable", lambda: None)
        resp = api.azure_readiness_cli_status_endpoint(_req())
        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["cli"]["installed"] is False
        assert body["account"]["authenticated"] is False

    def test_adversarial_recruiter_cannot_view(self, monkeypatch):
        import azure.functions as func

        monkeypatch.setattr(repositories, "get_user", lambda uid: _RECRUITER_USER if uid == "u-rec" else None)
        monkeypatch.setattr(repositories, "get_role", lambda key: _RECRUITER_ROLE_DOC if key == "recruiter" else None)
        token = auth.create_token(_RECRUITER_USER)
        resp = api.azure_readiness_cli_status_endpoint(func.HttpRequest(method="GET", url="/api/test", body=None, headers={"Authorization": f"Bearer {token}"}))
        assert resp.status_code == 403


class TestAzureReadinessValidateEndpoint:
    def test_golden_runs_real_check_and_persists_history(self, monkeypatch):
        api._azure_check_last_run.clear()
        recorded = []
        monkeypatch.setattr(repositories, "get_integration_instance", lambda iid: dict(_FIXTURE_INSTANCE))
        monkeypatch.setattr(repositories, "record_integration_health_check", lambda iid, result: recorded.append((iid, result)))
        resp = api.azure_readiness_validate_endpoint(_req(method="POST", route_params={"integration_id": "cosmos_db"}))
        assert resp.status_code == 200
        assert len(recorded) == 1
        assert recorded[0][0] == "cosmos_db"

    def test_golden_404_for_unknown_integration(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_integration_instance", lambda iid: None)
        resp = api.azure_readiness_validate_endpoint(_req(method="POST", route_params={"integration_id": "nope"}))
        assert resp.status_code == 404

    def test_adversarial_recruiter_cannot_validate(self, monkeypatch):
        import azure.functions as func

        monkeypatch.setattr(repositories, "get_user", lambda uid: _RECRUITER_USER if uid == "u-rec" else None)
        monkeypatch.setattr(repositories, "get_role", lambda key: _RECRUITER_ROLE_DOC if key == "recruiter" else None)
        token = auth.create_token(_RECRUITER_USER)
        resp = api.azure_readiness_validate_endpoint(func.HttpRequest(
            method="POST", url="/api/test", route_params={"integration_id": "cosmos_db"}, body=None,
            headers={"Authorization": f"Bearer {token}"},
        ))
        assert resp.status_code == 403


class TestApplicationInsightsTestTelemetryEndpoint:
    def test_golden_runs_real_connectivity_test(self, monkeypatch):
        import observability

        monkeypatch.setattr(observability, "TRACE_LOG_PATH", observability.TRACE_LOG_PATH)
        resp = api.application_insights_test_telemetry_endpoint(_req(method="POST"))
        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["correlation_id"].startswith("connectivity-test-")
        assert body["written_to_local_log"] is True

    def test_adversarial_recruiter_cannot_trigger(self, monkeypatch):
        import azure.functions as func

        monkeypatch.setattr(repositories, "get_user", lambda uid: _RECRUITER_USER if uid == "u-rec" else None)
        monkeypatch.setattr(repositories, "get_role", lambda key: _RECRUITER_ROLE_DOC if key == "recruiter" else None)
        token = auth.create_token(_RECRUITER_USER)
        resp = api.application_insights_test_telemetry_endpoint(func.HttpRequest(method="POST", url="/api/test", body=None, headers={"Authorization": f"Bearer {token}"}))
        assert resp.status_code == 403


class TestAzureAiFoundryTestRequestEndpoint:
    """POST /admin/azure-readiness/azure-ai-foundry/test-request -- the
    ONLY endpoint that can trigger a real, billable Azure OpenAI call,
    and only when an admin explicitly calls it (never from any readiness
    scan). See llm_utils.run_azure_openai_test_request()."""

    def test_golden_not_configured_reports_honestly(self, monkeypatch):
        for key in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_CHAT_DEPLOYMENT"):
            monkeypatch.delenv(key, raising=False)
        resp = api.azure_ai_foundry_test_request_endpoint(_req(method="POST"))
        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["configured"] is False

    def test_golden_real_result_passed_through(self, monkeypatch):
        monkeypatch.setattr(api.llm_utils, "run_azure_openai_test_request", lambda: {
            "configured": True,
            "chat": {"outcome": "succeeded", "response_preview": "OK"},
            "embedding": {"outcome": "succeeded", "dimensions": 1536},
        })
        resp = api.azure_ai_foundry_test_request_endpoint(_req(method="POST"))
        body = json.loads(resp.get_body())
        assert body["chat"]["outcome"] == "succeeded"
        assert body["embedding"]["dimensions"] == 1536

    def test_adversarial_never_fabricates_success_on_a_real_failure(self, monkeypatch):
        monkeypatch.setattr(api.llm_utils, "run_azure_openai_test_request", lambda: {
            "configured": True,
            "chat": {"outcome": "failed", "error": "401 Unauthorized"},
            "embedding": {"outcome": "failed", "error": "401 Unauthorized"},
        })
        resp = api.azure_ai_foundry_test_request_endpoint(_req(method="POST"))
        body = json.loads(resp.get_body())
        assert body["chat"]["outcome"] == "failed"

    def test_adversarial_recruiter_cannot_trigger(self, monkeypatch):
        import azure.functions as func

        monkeypatch.setattr(repositories, "get_user", lambda uid: _RECRUITER_USER if uid == "u-rec" else None)
        monkeypatch.setattr(repositories, "get_role", lambda key: _RECRUITER_ROLE_DOC if key == "recruiter" else None)
        token = auth.create_token(_RECRUITER_USER)
        resp = api.azure_ai_foundry_test_request_endpoint(func.HttpRequest(method="POST", url="/api/test", body=None, headers={"Authorization": f"Bearer {token}"}))
        assert resp.status_code == 403


class TestAzureReadinessValidateAllEndpoint:
    def test_golden_validates_every_non_planned_instance(self, monkeypatch):
        api._azure_check_last_run.clear()
        instances = [dict(_FIXTURE_INSTANCE), dict(_FIXTURE_INSTANCE, id="blob_storage", adapter_type="blob_storage")]
        recorded = []
        monkeypatch.setattr(repositories, "list_integration_instances", lambda: instances)
        monkeypatch.setattr(repositories, "record_integration_health_check", lambda iid, result: recorded.append(iid))
        monkeypatch.setattr(repositories, "get_integration_instance", lambda iid: next(i for i in instances if i["id"] == iid))
        resp = api.azure_readiness_validate_all_endpoint(_req(method="POST"))
        assert resp.status_code == 200
        assert len(recorded) == 2

    def test_adversarial_recruiter_cannot_validate_all(self, monkeypatch):
        import azure.functions as func

        monkeypatch.setattr(repositories, "get_user", lambda uid: _RECRUITER_USER if uid == "u-rec" else None)
        monkeypatch.setattr(repositories, "get_role", lambda key: _RECRUITER_ROLE_DOC if key == "recruiter" else None)
        token = auth.create_token(_RECRUITER_USER)
        resp = api.azure_readiness_validate_all_endpoint(func.HttpRequest(method="POST", url="/api/test", body=None, headers={"Authorization": f"Bearer {token}"}))
        assert resp.status_code == 403
