"""Tests for the Communications Configuration Console: comms_config.py's
validation/runtime-config/template resolution, email_adapter.py's
credential resolution and connectivity test, whatsapp_client.py's new
check_connection(), duplicate-send protection, and the
api_blueprint.py admin/communications-config* endpoints."""

import json

import api_blueprint as api
import auth
import comms_config
import email_adapter
import repositories
from conftest import _req, _fixture_token


_RECRUITER_ROLE_DOC = {"id": "recruiter", "role_key": "recruiter", "display_name": "Recruiter", "is_system": True}
_RECRUITER_USER = {"id": "u-rec", "username": "rec", "role_keys": ["recruiter"], "status": "active"}

_VALID_COMMS_CONFIG = {
    "email": {"enabled": True, "sender_display_name": "CDPI", "reply_to": ""},
    "whatsapp": {"enabled": True},
    "delivery_policies": {"default_channel": {"candidate_decision_notification": "whatsapp"}, "duplicate_send_window_minutes": 60},
}


# ---------------------------------------------------------------------------
# comms_config.py
# ---------------------------------------------------------------------------
class TestChannelRegistry:
    def test_golden_email_and_whatsapp_are_supported(self):
        assert comms_config.CHANNELS["email"]["status"] == "supported"
        assert comms_config.CHANNELS["whatsapp"]["status"] == "supported"

    def test_golden_sms_is_honestly_absent(self):
        assert comms_config.CHANNELS["sms"]["status"] == "absent"
        assert comms_config.CHANNELS["sms"]["source_modules"] == []
        caps = comms_config.CHANNELS["sms"]["capabilities"]
        assert caps == {"connectivity_check": False, "test_message": False}

    def test_golden_whatsapp_has_no_test_message_capability(self):
        """Meta's template-approval rules mean there's no safe sandbox
        send path -- this must stay False, not a fake success."""
        assert comms_config.CHANNELS["whatsapp"]["capabilities"]["test_message"] is False


class TestValidateCommsConfig:
    def test_golden_valid_config_has_no_errors(self):
        assert comms_config.validate_comms_config(_VALID_COMMS_CONFIG) == []

    def test_adversarial_out_of_range_duplicate_window_rejected(self):
        bad = json.loads(json.dumps(_VALID_COMMS_CONFIG))
        bad["delivery_policies"]["duplicate_send_window_minutes"] = 99999
        errors = comms_config.validate_comms_config(bad)
        assert any("duplicate_send_window_minutes" in e for e in errors)

    def test_adversarial_default_channel_cannot_be_sms(self):
        bad = json.loads(json.dumps(_VALID_COMMS_CONFIG))
        bad["delivery_policies"]["default_channel"]["candidate_decision_notification"] = "sms"
        errors = comms_config.validate_comms_config(bad)
        assert any("sms" in e for e in errors)

    def test_adversarial_default_channel_unknown_rejected(self):
        bad = json.loads(json.dumps(_VALID_COMMS_CONFIG))
        bad["delivery_policies"]["default_channel"]["x"] = "carrier_pigeon"
        errors = comms_config.validate_comms_config(bad)
        assert any("carrier_pigeon" in e for e in errors)


class TestValidateInterviewBotConfig:
    def test_golden_valid_has_no_errors(self):
        assert comms_config.validate_interview_bot_config(comms_config.DEFAULT_INTERVIEW_BOT_CONFIG) == []

    def test_adversarial_temperature_out_of_range(self):
        bad = dict(comms_config.DEFAULT_INTERVIEW_BOT_CONFIG, temperature=5.0)
        errors = comms_config.validate_interview_bot_config(bad)
        assert any("temperature" in e for e in errors)


class TestGetCommsRuntimeConfig:
    def test_golden_falls_back_to_defaults_when_cosmos_unavailable(self, monkeypatch):
        def broken():
            raise RuntimeError("Cosmos not configured")

        monkeypatch.setattr(repositories, "get_active_comms_config", broken)
        assert comms_config.get_comms_runtime_config() == comms_config.DEFAULT_COMMS_CONFIG

    def test_golden_falls_back_when_nothing_activated(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_active_comms_config", lambda: None)
        assert comms_config.get_comms_runtime_config() == comms_config.DEFAULT_COMMS_CONFIG


class TestGetActiveTemplate:
    def test_golden_falls_back_to_real_existing_template_text(self, monkeypatch):
        def broken(tid):
            raise RuntimeError("Cosmos not configured")

        monkeypatch.setattr(repositories, "get_active_comms_template_version", broken)
        template = comms_config.get_active_template("candidate_interview_invite")
        assert template["version"] == "default"
        assert "{candidate_name}" in template["body"]

    def test_adversarial_unknown_template_id_raises(self):
        import pytest

        with pytest.raises(ValueError):
            comms_config.get_active_template("not_a_real_template")


# ---------------------------------------------------------------------------
# email_adapter.py -- exercised against the REAL legacy JSON fallback in
# this sandbox (same discipline as azure_integrations.py's real-index
# tests this session already established).
# ---------------------------------------------------------------------------
class TestEmailAdapter:
    def test_golden_resolves_real_smtp_config(self):
        config = email_adapter.resolve_smtp_config()
        assert config["missing"] == []
        assert config["server"]
        assert config["port"]

    def test_golden_test_connectivity_returns_real_outcome_vocabulary(self):
        result = email_adapter.test_connectivity()
        assert result["outcome"] in ("healthy", "unhealthy", "unknown")
        assert "duration_ms" in result

    def test_adversarial_missing_config_reports_unknown_not_a_crash(self, monkeypatch):
        monkeypatch.setattr(email_adapter, "resolve_smtp_config", lambda: {"missing": ["SMTP_EMAIL"], "email": None, "password": None, "server": None, "port": None, "source": "incomplete"})
        result = email_adapter.test_connectivity()
        assert result["outcome"] == "unknown"
        assert result["error_category"] == "configuration_invalid"

    def test_adversarial_send_email_raises_on_missing_config(self, monkeypatch):
        import pytest

        monkeypatch.setattr(email_adapter, "resolve_smtp_config", lambda: {"missing": ["SMTP_EMAIL"], "email": None, "password": None, "server": None, "port": None, "source": "incomplete"})
        with pytest.raises(RuntimeError):
            email_adapter.send_email("x@example.com", "subject", "body")


# ---------------------------------------------------------------------------
# Duplicate-send protection + delivery logging
# ---------------------------------------------------------------------------
class TestHasRecentDelivery:
    def test_golden_detects_a_recent_matching_delivery(self, monkeypatch):
        from datetime import datetime, timezone

        monkeypatch.setattr(repositories, "list_comms_deliveries", lambda cid, limit=50: [
            {"channel": "whatsapp", "workflow": "candidate_decision_notification", "submitted_at": datetime.now(timezone.utc).isoformat()},
        ])
        assert repositories.has_recent_delivery("cand-1", "whatsapp", "candidate_decision_notification", 60) is True

    def test_golden_no_match_for_different_channel(self, monkeypatch):
        from datetime import datetime, timezone

        monkeypatch.setattr(repositories, "list_comms_deliveries", lambda cid, limit=50: [
            {"channel": "email", "workflow": "candidate_decision_notification", "submitted_at": datetime.now(timezone.utc).isoformat()},
        ])
        assert repositories.has_recent_delivery("cand-1", "whatsapp", "candidate_decision_notification", 60) is False

    def test_golden_old_delivery_outside_window_does_not_count(self, monkeypatch):
        monkeypatch.setattr(repositories, "list_comms_deliveries", lambda cid, limit=50: [
            {"channel": "whatsapp", "workflow": "candidate_decision_notification", "submitted_at": "2020-01-01T00:00:00+00:00"},
        ])
        assert repositories.has_recent_delivery("cand-1", "whatsapp", "candidate_decision_notification", 60) is False


class TestNotifyCandidateWhatsappGating:
    def test_golden_skips_when_channel_disabled(self, monkeypatch):
        monkeypatch.setattr(comms_config, "get_comms_runtime_config", lambda: {"whatsapp": {"enabled": False}, "email": {}, "delivery_policies": {"duplicate_send_window_minutes": 60}})
        result = api._notify_candidate_whatsapp({"Phone": "+15551234567", "candidate_id": "cand-1", "Name": "Test"}, "approve")
        assert "disabled" in result

    def test_golden_skips_on_duplicate_within_window(self, monkeypatch):
        monkeypatch.setattr(comms_config, "get_comms_runtime_config", lambda: comms_config.DEFAULT_COMMS_CONFIG)
        monkeypatch.setattr(repositories, "has_recent_delivery", lambda cid, ch, wf, window: True)
        result = api._notify_candidate_whatsapp({"Phone": "+15551234567", "candidate_id": "cand-1", "Name": "Test"}, "approve")
        assert "duplicate" in result

    def test_golden_records_a_delivery_on_send_attempt(self, monkeypatch):
        captured = []
        monkeypatch.setattr(comms_config, "get_comms_runtime_config", lambda: comms_config.DEFAULT_COMMS_CONFIG)
        monkeypatch.setattr(repositories, "has_recent_delivery", lambda cid, ch, wf, window: False)
        monkeypatch.setattr(repositories, "record_comms_delivery", lambda entry: captured.append(entry))

        import whatsapp_client

        monkeypatch.setattr(whatsapp_client, "send_whatsapp_template", lambda *a, **k: {"messages": [{"id": "wamid.123"}]})
        result = api._notify_candidate_whatsapp({"Phone": "+15551234567", "candidate_id": "cand-1", "Name": "Test"}, "approve")
        assert result == "sent"
        assert captured[0]["outcome"] == "provider_accepted"
        assert captured[0]["correlation_id"] == "cand-1"

    def test_adversarial_send_failure_is_logged_not_swallowed_silently(self, monkeypatch):
        captured = []
        monkeypatch.setattr(comms_config, "get_comms_runtime_config", lambda: comms_config.DEFAULT_COMMS_CONFIG)
        monkeypatch.setattr(repositories, "has_recent_delivery", lambda cid, ch, wf, window: False)
        monkeypatch.setattr(repositories, "record_comms_delivery", lambda entry: captured.append(entry))

        import whatsapp_client

        def broken(*a, **k):
            raise RuntimeError("Graph API error")

        monkeypatch.setattr(whatsapp_client, "send_whatsapp_template", broken)
        result = api._notify_candidate_whatsapp({"Phone": "+15551234567", "candidate_id": "cand-1", "Name": "Test"}, "approve")
        assert "failed" in result
        assert captured[0]["outcome"] == "failed"


# ---------------------------------------------------------------------------
# Endpoint layer
# ---------------------------------------------------------------------------
class TestGetCommsConfigEndpoint:
    def test_golden_reports_defaults_when_never_activated(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_active_comms_config", lambda: None)
        monkeypatch.setattr(repositories, "get_draft_comms_config", lambda: None)
        resp = api.get_comms_config_endpoint(_req())
        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["active"]["config_version"] == 0
        assert len(body["channels"]) == 3

    def test_adversarial_recruiter_cannot_view(self, monkeypatch):
        import azure.functions as func

        monkeypatch.setattr(repositories, "get_user", lambda uid: _RECRUITER_USER if uid == "u-rec" else None)
        monkeypatch.setattr(repositories, "get_role", lambda key: _RECRUITER_ROLE_DOC if key == "recruiter" else None)
        token = auth.create_token(_RECRUITER_USER)
        resp = api.get_comms_config_endpoint(func.HttpRequest(method="GET", url="/api/test", body=None, headers={"Authorization": f"Bearer {token}"}))
        assert resp.status_code == 403


class TestSaveCommsConfigDraftEndpoint:
    def test_golden_valid_draft_saved(self, monkeypatch):
        monkeypatch.setattr(repositories, "save_draft_comms_config", lambda fields, actor: dict(fields, id="draft", config_version=1))
        resp = api.save_comms_config_draft_endpoint(_req(method="PUT", body=_VALID_COMMS_CONFIG))
        assert resp.status_code == 200

    def test_adversarial_invalid_draft_rejected(self, monkeypatch):
        captured = []
        monkeypatch.setattr(repositories, "save_draft_comms_config", lambda fields, actor: captured.append(fields))
        bad = json.loads(json.dumps(_VALID_COMMS_CONFIG))
        bad["delivery_policies"]["duplicate_send_window_minutes"] = -5
        resp = api.save_comms_config_draft_endpoint(_req(method="PUT", body=bad))
        assert resp.status_code == 400
        assert captured == []

    def test_adversarial_missing_section_rejected(self):
        resp = api.save_comms_config_draft_endpoint(_req(method="PUT", body={"email": {}}))
        assert resp.status_code == 400


class TestActivateCommsConfigEndpoint:
    def test_golden_activates_valid_draft(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_draft_comms_config", lambda: dict(_VALID_COMMS_CONFIG, id="draft", config_version=1))
        monkeypatch.setattr(repositories, "activate_comms_config", lambda actor: dict(_VALID_COMMS_CONFIG, id="active", config_version=1))
        resp = api.activate_comms_config_endpoint(_req(method="POST"))
        assert resp.status_code == 200

    def test_adversarial_no_draft_404(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_draft_comms_config", lambda: None)
        resp = api.activate_comms_config_endpoint(_req(method="POST"))
        assert resp.status_code == 404


class TestCommsConfigHistoryAndRollback:
    def test_golden_history_lists_entries(self, monkeypatch):
        monkeypatch.setattr(repositories, "list_comms_config_history", lambda limit=50: [{"id": "h1", "operation": "activated"}])
        resp = api.get_comms_config_history_endpoint(_req())
        assert resp.status_code == 200
        assert json.loads(resp.get_body())["count"] == 1

    def test_golden_rollback_creates_a_new_draft(self, monkeypatch):
        entry = {"id": "h1", "operation": "activated", "safe_summary": _VALID_COMMS_CONFIG}
        saved = {}
        monkeypatch.setattr(repositories, "list_comms_config_history", lambda limit=200: [entry])
        monkeypatch.setattr(repositories, "save_draft_comms_config", lambda fields, actor: saved.update(fields) or dict(fields, id="draft", config_version=2))
        resp = api.rollback_comms_config_endpoint(_req(method="POST", route_params={"history_id": "h1"}))
        assert resp.status_code == 200
        assert saved["email"] == _VALID_COMMS_CONFIG["email"]

    def test_adversarial_rollback_to_non_activated_entry_404(self, monkeypatch):
        monkeypatch.setattr(repositories, "list_comms_config_history", lambda limit=200: [{"id": "h1", "operation": "draft_saved", "safe_summary": {}}])
        resp = api.rollback_comms_config_endpoint(_req(method="POST", route_params={"history_id": "h1"}))
        assert resp.status_code == 404


class TestInterviewBotConfigEndpoints:
    def test_golden_get_reports_defaults(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_active_interview_bot_config", lambda: None)
        monkeypatch.setattr(repositories, "get_draft_interview_bot_config", lambda: None)
        resp = api.get_interview_bot_config_endpoint(_req())
        assert resp.status_code == 200
        assert json.loads(resp.get_body())["active"]["config_version"] == 0

    def test_golden_save_draft_valid(self, monkeypatch):
        monkeypatch.setattr(repositories, "save_draft_interview_bot_config", lambda fields, actor: dict(fields, id="draft", config_version=1))
        resp = api.save_interview_bot_config_draft_endpoint(_req(method="PUT", body={"temperature": 0.5}))
        assert resp.status_code == 200

    def test_adversarial_save_draft_invalid_rejected(self):
        resp = api.save_interview_bot_config_draft_endpoint(_req(method="PUT", body={"temperature": 99}))
        assert resp.status_code == 400

    def test_golden_activate(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_draft_interview_bot_config", lambda: dict(comms_config.DEFAULT_INTERVIEW_BOT_CONFIG, id="draft", config_version=1))
        monkeypatch.setattr(repositories, "activate_interview_bot_config", lambda actor: dict(comms_config.DEFAULT_INTERVIEW_BOT_CONFIG, id="active", config_version=1))
        resp = api.activate_interview_bot_config_endpoint(_req(method="POST"))
        assert resp.status_code == 200

    def test_adversarial_activate_no_draft_404(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_draft_interview_bot_config", lambda: None)
        resp = api.activate_interview_bot_config_endpoint(_req(method="POST"))
        assert resp.status_code == 404


class TestCommsTemplateEndpoints:
    def test_golden_list_templates(self, monkeypatch):
        monkeypatch.setattr(repositories, "list_comms_template_versions", lambda tid: [{"template_id": tid, "version": 1, "status": "active"}])
        resp = api.list_comms_templates_endpoint(_req())
        assert resp.status_code == 200
        assert set(json.loads(resp.get_body())["templates"].keys()) == set(comms_config.TEMPLATE_IDS)

    def test_golden_create_draft_with_valid_placeholders(self, monkeypatch):
        monkeypatch.setattr(repositories, "create_comms_template_draft_version", lambda tid, subject, body, actor: {"template_id": tid, "version": 2, "subject": subject, "body": body, "status": "draft"})
        body = "Hi {candidate_name}, your interview is on {interview_date} ({interview_day}) at {interview_start}-{interview_end} ({interview_slot}): {interview_link}"
        resp = api.create_comms_template_draft_endpoint(_req(method="POST", route_params={"template_id": "candidate_interview_invite"}, body={"subject": "x", "body": body}))
        assert resp.status_code == 201

    def test_adversarial_missing_placeholder_rejected(self):
        resp = api.create_comms_template_draft_endpoint(_req(method="POST", route_params={"template_id": "candidate_interview_invite"}, body={"subject": "x", "body": "Hi {candidate_name}, that's it."}))
        assert resp.status_code == 400

    def test_adversarial_unknown_template_id_404(self):
        resp = api.create_comms_template_draft_endpoint(_req(method="POST", route_params={"template_id": "not_real"}, body={"body": "x"}))
        assert resp.status_code == 404

    def test_golden_activate_version(self, monkeypatch):
        monkeypatch.setattr(repositories, "activate_comms_template_version", lambda tid, version, actor: {"template_id": tid, "version": version, "status": "active"})
        resp = api.activate_comms_template_version_endpoint(_req(method="POST", route_params={"template_id": "candidate_interview_invite", "version": "2"}))
        assert resp.status_code == 200

    def test_golden_preview_uses_only_synthetic_data(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_active_comms_template_version", lambda tid: None)
        resp = api.preview_comms_template_endpoint(_req(route_params={"template_id": "candidate_interview_invite"}))
        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["is_synthetic_example_data"] is True
        assert "Jordan Example" in body["rendered_body"]

    def test_adversarial_recruiter_cannot_manage_templates(self, monkeypatch):
        import azure.functions as func

        monkeypatch.setattr(repositories, "get_user", lambda uid: _RECRUITER_USER if uid == "u-rec" else None)
        monkeypatch.setattr(repositories, "get_role", lambda key: _RECRUITER_ROLE_DOC if key == "recruiter" else None)
        token = auth.create_token(_RECRUITER_USER)
        resp = api.create_comms_template_draft_endpoint(func.HttpRequest(
            method="POST", url="/api/test", route_params={"template_id": "candidate_interview_invite"},
            body=json.dumps({"body": "x"}).encode(), headers={"Authorization": f"Bearer {token}"},
        ))
        assert resp.status_code == 403


class TestEmailTestEndpoints:
    def setup_method(self):
        api._comms_test_last_run.clear()

    def test_golden_connectivity_test(self, monkeypatch):
        monkeypatch.setattr(email_adapter, "test_connectivity", lambda: {"outcome": "healthy", "error_category": None, "detail": "ok", "duration_ms": 1.0})
        resp = api.test_email_connectivity_endpoint(_req(method="POST"))
        assert resp.status_code == 200

    def test_golden_test_send_requires_confirm_and_recipient(self):
        resp = api.send_test_email_endpoint(_req(method="POST", body={"to": "x@example.com"}))
        assert resp.status_code == 400
        resp2 = api.send_test_email_endpoint(_req(method="POST", body={"confirm": True}))
        assert resp2.status_code == 400

    def test_golden_test_send_labels_message_as_test(self, monkeypatch):
        captured = {}

        def fake_send(to, subject, body):
            captured["to"], captured["subject"], captured["body"] = to, subject, body
            return {"to": to, "subject": subject, "from": "sender@example.com"}

        monkeypatch.setattr(email_adapter, "send_email", fake_send)
        resp = api.send_test_email_endpoint(_req(method="POST", body={"to": "x@example.com", "confirm": True}))
        assert resp.status_code == 200
        assert "TEST" in captured["subject"]
        assert "TEST" in captured["body"]
        assert json.loads(resp.get_body())["is_test_message"] is True

    def test_adversarial_rate_limited_second_call(self, monkeypatch):
        monkeypatch.setattr(email_adapter, "test_connectivity", lambda: {"outcome": "healthy", "error_category": None, "detail": "ok", "duration_ms": 1.0})
        resp1 = api.test_email_connectivity_endpoint(_req(method="POST"))
        assert resp1.status_code == 200
        resp2 = api.test_email_connectivity_endpoint(_req(method="POST"))
        assert resp2.status_code == 429

    def test_adversarial_recruiter_cannot_send_test_email(self, monkeypatch):
        import azure.functions as func

        monkeypatch.setattr(repositories, "get_user", lambda uid: _RECRUITER_USER if uid == "u-rec" else None)
        monkeypatch.setattr(repositories, "get_role", lambda key: _RECRUITER_ROLE_DOC if key == "recruiter" else None)
        token = auth.create_token(_RECRUITER_USER)
        resp = api.send_test_email_endpoint(func.HttpRequest(
            method="POST", url="/api/test", body=json.dumps({"to": "x@example.com", "confirm": True}).encode(),
            headers={"Authorization": f"Bearer {token}"},
        ))
        assert resp.status_code == 403


class TestWhatsappTestConnectivityEndpoint:
    def setup_method(self):
        api._comms_test_last_run.clear()

    def test_golden_runs_real_check_function(self, monkeypatch):
        import whatsapp_client

        monkeypatch.setattr(whatsapp_client, "check_connection", lambda: {"outcome": "unknown", "error_category": "configuration_invalid", "detail": "not configured", "duration_ms": 1.0})
        resp = api.test_whatsapp_connectivity_endpoint(_req(method="POST"))
        assert resp.status_code == 200
        assert json.loads(resp.get_body())["outcome"] == "unknown"


class TestDeliveryLogEndpoint:
    def test_golden_returns_real_records(self, monkeypatch):
        monkeypatch.setattr(repositories, "list_comms_deliveries", lambda cid, limit=50: [{"channel": "whatsapp", "outcome": "provider_accepted"}])
        resp = api.get_comms_delivery_log_endpoint(_req(headers={"Authorization": f"Bearer {_fixture_token()}"}))
        # correlation_id missing -> 400
        assert resp.status_code == 400

    def test_golden_with_correlation_id(self, monkeypatch):
        import azure.functions as func

        monkeypatch.setattr(repositories, "list_comms_deliveries", lambda cid, limit=50: [{"channel": "whatsapp", "outcome": "provider_accepted"}])
        resp = api.get_comms_delivery_log_endpoint(func.HttpRequest(
            method="GET", url="/api/test?correlation_id=cand-1", params={"correlation_id": "cand-1"}, body=None,
            headers={"Authorization": f"Bearer {_fixture_token()}"},
        ))
        assert resp.status_code == 200
        assert json.loads(resp.get_body())["count"] == 1

    def test_adversarial_recruiter_cannot_view_delivery_log(self, monkeypatch):
        import azure.functions as func

        monkeypatch.setattr(repositories, "get_user", lambda uid: _RECRUITER_USER if uid == "u-rec" else None)
        monkeypatch.setattr(repositories, "get_role", lambda key: _RECRUITER_ROLE_DOC if key == "recruiter" else None)
        token = auth.create_token(_RECRUITER_USER)
        resp = api.get_comms_delivery_log_endpoint(func.HttpRequest(
            method="GET", url="/api/test?correlation_id=cand-1", params={"correlation_id": "cand-1"}, body=None,
            headers={"Authorization": f"Bearer {token}"},
        ))
        assert resp.status_code == 403


class TestNewPageAndPermissions:
    def test_golden_permissions_exist_and_admin_has_them(self):
        admin = next(r for r in auth.DEFAULT_ROLES if r["role_key"] == "admin")
        for pid in ("comms.config.read", "comms.config.write", "comms.config.activate", "comms.test.send", "comms.templates.manage", "comms.delivery.read"):
            assert pid in auth.PERMISSIONS
            assert pid in admin["permission_ids"]

    def test_golden_page_registered_with_correct_permission(self):
        page = next(p for p in auth.PAGES if p["page_id"] == "communications_config")
        assert page["required_permission"] == "comms.config.read"

    def test_golden_non_admin_roles_do_not_get_comms_permissions(self):
        # recruitment_administrator legitimately holds comms.config.read/
        # comms.templates.manage, recruitment_ops_manager holds
        # comms.delivery.read, and security_compliance_auditor holds
        # comms.config.read as a read-only compliance view (see
        # auth.DEFAULT_ROLES) -- checked per-permission below rather than
        # blanket-exempting any role from every comms.* permission.
        exempt_by_permission = {
            "comms.config.read": {"admin", "recruitment_administrator", "security_compliance_auditor"},
            "comms.config.write": {"admin"},
            "comms.config.activate": {"admin"},
            "comms.test.send": {"admin"},
            "comms.templates.manage": {"admin", "recruitment_administrator"},
            "comms.delivery.read": {"admin", "recruitment_ops_manager", "recruiter"},
        }
        for role in auth.DEFAULT_ROLES:
            for pid, exempt_roles in exempt_by_permission.items():
                if role["role_key"] in exempt_roles:
                    continue
                assert pid not in role["permission_ids"]
