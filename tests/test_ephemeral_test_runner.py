"""Tests for ephemeral_test_runner.py (Mode C: provision -> test ->
destroy) and its /admin/azure-readiness/ephemeral-tests* endpoints.

No real Azure CLI exists in this test environment, so execute_run()/
cleanup_run() are tested two ways: (1) against the REAL az_cli module,
asserting the honest BLOCKED behavior this sandbox actually produces
(the same evidence-based approach az_cli's own tests use), and (2) with
subprocess mocked, to exercise the deeper lifecycle logic (resource
group collision, ownership-tag check, cleanup polling) that a real `az`
binary would be needed for otherwise."""

import json

import api_blueprint as api
import auth
import ephemeral_test_runner as runner
import repositories
from conftest import _req


_RECRUITER_ROLE_DOC = {"id": "recruiter", "role_key": "recruiter", "display_name": "Recruiter", "is_system": True}
_RECRUITER_USER = {"id": "u-rec", "username": "rec", "role_keys": ["recruiter"], "status": "active"}


class TestGeneratePlan:
    def test_golden_generates_a_plan_awaiting_approval(self):
        plan = runner.generate_plan("blob_storage_test", "eastus", "admin")
        assert plan["status"] == "AWAITING_APPROVAL"
        assert plan["approved"] is False
        assert plan["resource_group_name"].startswith("rg-cdpi-ephemeral-")
        assert plan["tags"]["testRunId"] == plan["run_id"]

    def test_golden_two_plans_never_collide(self):
        a = runner.generate_plan("blob_storage_test", "eastus", "admin")
        b = runner.generate_plan("blob_storage_test", "eastus", "admin")
        assert a["run_id"] != b["run_id"]
        assert a["resource_group_name"] != b["resource_group_name"]

    def test_adversarial_unknown_resource_plan_rejected(self):
        try:
            runner.generate_plan("every_azure_service_at_once", "eastus", "admin")
            assert False, "should have raised"
        except ValueError as e:
            assert "Unknown or unapproved" in str(e)

    def test_adversarial_disallowed_region_rejected(self):
        try:
            runner.generate_plan("blob_storage_test", "antarctica-south", "admin")
            assert False, "should have raised"
        except ValueError as e:
            assert "allowed region" in str(e)


class TestExecuteRunAuthGate:
    """Whether a real `az` CLI happens to be installed/authenticated on
    the machine running this suite must never change what these tests
    exercise -- forcing az_cli.get_account_status() explicitly (rather
    than relying on ambient sandbox state, as an earlier version of this
    test did) is what guarantees an automated test run can never make a
    real mutating Azure API call, even when a real authenticated CLI
    session happens to exist (found to matter for real during the Azure
    real-services audit pass: a real `az login` session in this sandbox
    made the previous, environment-dependent version of this test
    actually attempt `az group create` against the real subscription --
    it failed closed on an expired token, but should never have reached
    that call in an automated test in the first place)."""

    def test_golden_blocks_on_missing_approval(self):
        plan = runner.generate_plan("blob_storage_test", "eastus", "admin")
        result = runner.execute_run(plan, "admin")
        assert result["status"] == "BLOCKED"
        assert "not been explicitly approved" in result["blocked_reason"]

    def test_golden_blocks_on_missing_az_cli_authentication(self, monkeypatch):
        monkeypatch.setattr(runner.az_cli, "get_account_status", lambda: {"authenticated": False, "detail": "No active Azure CLI session."})
        plan = runner.generate_plan("blob_storage_test", "eastus", "admin")
        plan["approved"] = True
        result = runner.execute_run(plan, "admin")
        assert result["status"] == "BLOCKED"
        assert "authentication" in result["blocked_reason"].lower()

    def test_adversarial_never_reports_provisioned_without_a_real_deployment(self, monkeypatch):
        monkeypatch.setattr(runner.az_cli, "get_account_status", lambda: {"authenticated": False, "detail": "No active Azure CLI session."})
        plan = runner.generate_plan("blob_storage_test", "eastus", "admin")
        plan["approved"] = True
        result = runner.execute_run(plan, "admin")
        assert result["status"] not in ("PROVISIONED", "TEST_PASSED")
        assert result["resources_actually_created"] == []

    def test_adversarial_authenticated_but_unapproved_still_blocks_before_any_az_call(self, monkeypatch):
        # Even with a REAL authenticated session, an unapproved plan must
        # never reach a single az subprocess call -- approval is checked
        # first, unconditionally.
        calls = []
        monkeypatch.setattr(runner.az_cli, "get_account_status", lambda: {"authenticated": True, "detail": "ok"})
        monkeypatch.setattr(runner, "_run_az", lambda *a, **k: calls.append(a) or (0, "", ""))
        plan = runner.generate_plan("blob_storage_test", "eastus", "admin")
        result = runner.execute_run(plan, "admin")
        assert result["status"] == "BLOCKED"
        assert calls == []


class TestExecuteRunMockedCli:
    def test_golden_refuses_to_reuse_an_existing_resource_group(self, monkeypatch):
        plan = runner.generate_plan("blob_storage_test", "eastus", "admin")
        plan["approved"] = True
        monkeypatch.setattr(runner.az_cli, "get_account_status", lambda: {"authenticated": True, "detail": "ok"})

        def fake_run_az(args, timeout=600):
            if args[:2] == ["group", "exists"]:
                return 0, "true\n", ""
            return 1, "", "should not reach here"
        monkeypatch.setattr(runner, "_run_az", fake_run_az)

        result = runner.execute_run(plan, "admin")
        assert result["status"] == "BLOCKED"
        assert "already exists" in result["blocked_reason"]

    def test_golden_full_lifecycle_to_test_passed(self, monkeypatch):
        plan = runner.generate_plan("blob_storage_test", "eastus", "admin")
        plan["approved"] = True
        monkeypatch.setattr(runner.az_cli, "get_account_status", lambda: {"authenticated": True, "detail": "ok"})

        calls = []

        def fake_run_az(args, timeout=600):
            calls.append(args)
            if args[:2] == ["group", "exists"]:
                return 0, "false\n", ""
            if args[:2] == ["group", "create"]:
                return 0, "", ""
            if args[:2] == ["deployment", "group"]:
                return 0, "{}", ""
            if args[:3] == ["storage", "account", "keys"]:
                return 0, json.dumps([{"value": "fake-key"}]), ""
            return 1, "", "unexpected call"
        monkeypatch.setattr(runner, "_run_az", fake_run_az)
        monkeypatch.setattr(
            runner, "_run_blob_storage_functional_test",
            lambda run: {"passed": True, "detail": "mocked blob round-trip succeeded"},
        )

        result = runner.execute_run(plan, "admin")
        assert result["status"] == "TEST_PASSED"
        assert result["resources_actually_created"][0]["name"] == plan["storage_account_name"]


class TestCleanupRun:
    def test_golden_already_gone_is_verified_immediately(self, monkeypatch):
        run = runner.generate_plan("blob_storage_test", "eastus", "admin")
        run["status"] = "TEST_PASSED"
        monkeypatch.setattr(runner, "_run_az", lambda args, timeout=30: (0, "false\n", ""))
        result = runner.cleanup_run(run, "admin")
        assert result["cleanup_status"] == "CLEANUP_VERIFIED"

    def test_adversarial_refuses_to_delete_mismatched_ownership_tag(self, monkeypatch):
        run = runner.generate_plan("blob_storage_test", "eastus", "admin")
        run["status"] = "TEST_PASSED"

        def fake_run_az(args, timeout=30):
            if args[:2] == ["group", "exists"]:
                return 0, "true\n", ""
            if args[:2] == ["group", "show"]:
                return 0, json.dumps({"testRunId": "someone-elses-run"}), ""
            return 1, "", "should not reach delete"
        monkeypatch.setattr(runner, "_run_az", fake_run_az)
        result = runner.cleanup_run(run, "admin")
        assert result["cleanup_status"] == "CLEANUP_FAILED"
        assert "does not match" in result["blocked_reason"]

    def test_golden_nothing_to_clean_up_for_a_blocked_run(self):
        run = runner.generate_plan("blob_storage_test", "eastus", "admin")
        run["status"] = "BLOCKED"
        result = runner.cleanup_run(run, "admin")
        assert result["cleanup_status"] == "CLEANUP_FAILED"
        assert "Nothing to clean up" in result["blocked_reason"]


class TestPlanEphemeralTestEndpoint:
    def test_golden_creates_and_persists_a_plan(self, monkeypatch):
        saved = {}
        monkeypatch.setattr(repositories, "create_ephemeral_test_run", lambda doc: dict(doc, **{"_saved": True}))
        resp = api.plan_ephemeral_test_endpoint(_req(method="POST", body={"resource_plan_id": "blob_storage_test", "region": "eastus"}))
        assert resp.status_code == 201
        body = json.loads(resp.get_body())
        assert body["status"] == "AWAITING_APPROVAL"

    def test_adversarial_unknown_plan_is_400_not_500(self):
        resp = api.plan_ephemeral_test_endpoint(_req(method="POST", body={"resource_plan_id": "nope", "region": "eastus"}))
        assert resp.status_code == 400

    def test_adversarial_recruiter_cannot_plan(self, monkeypatch):
        import azure.functions as func

        monkeypatch.setattr(repositories, "get_user", lambda uid: _RECRUITER_USER if uid == "u-rec" else None)
        monkeypatch.setattr(repositories, "get_role", lambda key: _RECRUITER_ROLE_DOC if key == "recruiter" else None)
        token = auth.create_token(_RECRUITER_USER)
        resp = api.plan_ephemeral_test_endpoint(func.HttpRequest(
            method="POST", url="/api/test", body=json.dumps({"resource_plan_id": "blob_storage_test", "region": "eastus"}).encode(),
            headers={"Authorization": f"Bearer {token}"},
        ))
        assert resp.status_code == 403


class TestApproveEphemeralTestEndpoint:
    def test_golden_approves_an_awaiting_run(self, monkeypatch):
        run = runner.generate_plan("blob_storage_test", "eastus", "admin")
        monkeypatch.setattr(repositories, "get_ephemeral_test_run", lambda rid: dict(run))
        monkeypatch.setattr(repositories, "update_ephemeral_test_run", lambda doc: doc)
        resp = api.approve_ephemeral_test_endpoint(_req(method="POST", route_params={"runId": run["run_id"]}))
        assert resp.status_code == 200
        assert json.loads(resp.get_body())["approved"] is True

    def test_adversarial_cannot_approve_an_already_provisioned_run(self, monkeypatch):
        run = runner.generate_plan("blob_storage_test", "eastus", "admin")
        run["status"] = "PROVISIONED"
        monkeypatch.setattr(repositories, "get_ephemeral_test_run", lambda rid: dict(run))
        resp = api.approve_ephemeral_test_endpoint(_req(method="POST", route_params={"runId": run["run_id"]}))
        assert resp.status_code == 400

    def test_golden_404_for_unknown_run(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_ephemeral_test_run", lambda rid: None)
        resp = api.approve_ephemeral_test_endpoint(_req(method="POST", route_params={"runId": "nope"}))
        assert resp.status_code == 404


class TestExecuteAndCleanupEndpointsRbac:
    def test_adversarial_recruiter_cannot_execute(self, monkeypatch):
        import azure.functions as func

        monkeypatch.setattr(repositories, "get_user", lambda uid: _RECRUITER_USER if uid == "u-rec" else None)
        monkeypatch.setattr(repositories, "get_role", lambda key: _RECRUITER_ROLE_DOC if key == "recruiter" else None)
        token = auth.create_token(_RECRUITER_USER)
        resp = api.execute_ephemeral_test_endpoint(func.HttpRequest(
            method="POST", url="/api/test", route_params={"runId": "x"}, body=None,
            headers={"Authorization": f"Bearer {token}"},
        ))
        assert resp.status_code == 403

    def test_adversarial_recruiter_cannot_cleanup(self, monkeypatch):
        import azure.functions as func

        monkeypatch.setattr(repositories, "get_user", lambda uid: _RECRUITER_USER if uid == "u-rec" else None)
        monkeypatch.setattr(repositories, "get_role", lambda key: _RECRUITER_ROLE_DOC if key == "recruiter" else None)
        token = auth.create_token(_RECRUITER_USER)
        resp = api.cleanup_ephemeral_test_endpoint(func.HttpRequest(
            method="POST", url="/api/test", route_params={"runId": "x"}, body=None,
            headers={"Authorization": f"Bearer {token}"},
        ))
        assert resp.status_code == 403

    def test_golden_execute_404_for_unknown_run(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_ephemeral_test_run", lambda rid: None)
        resp = api.execute_ephemeral_test_endpoint(_req(method="POST", route_params={"runId": "nope"}))
        assert resp.status_code == 404

    def test_golden_execute_blocks_without_az_cli_authentication(self, monkeypatch):
        monkeypatch.setattr(runner.az_cli, "get_account_status", lambda: {"authenticated": False, "detail": "No active Azure CLI session."})
        run = runner.generate_plan("blob_storage_test", "eastus", "admin")
        run["approved"] = True
        monkeypatch.setattr(repositories, "get_ephemeral_test_run", lambda rid: dict(run))
        monkeypatch.setattr(repositories, "update_ephemeral_test_run", lambda doc: doc)
        resp = api.execute_ephemeral_test_endpoint(_req(method="POST", route_params={"runId": run["run_id"]}))
        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["status"] == "BLOCKED"
        assert "authentication" in body["blocked_reason"].lower()
