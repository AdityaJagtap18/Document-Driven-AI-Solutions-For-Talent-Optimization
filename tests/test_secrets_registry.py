"""Tests for secrets_registry.py (the Secrets Management page's backend
catalog + live presence/source resolution) and the admin/secrets endpoint
in api_blueprint.py."""

import json

import api_blueprint as api
import auth
import repositories
import secrets_registry
from conftest import _req, _fixture_token


_RECRUITER_ROLE_DOC = {"id": "recruiter", "role_key": "recruiter", "display_name": "Recruiter", "is_system": True}
_RECRUITER_USER = {"id": "u-rec", "username": "rec", "role_keys": ["recruiter"], "status": "active"}


class TestSecretDefinitions:
    def test_golden_no_duplicate_keys(self):
        assert len(secrets_registry.SECRET_KEYS) == len(set(secrets_registry.SECRET_KEYS))

    def test_golden_every_definition_has_required_fields(self):
        for definition in secrets_registry.SECRET_DEFINITIONS:
            assert definition["key"]
            assert definition["label"]
            assert isinstance(definition["secret"], bool)
            assert isinstance(definition["used_by"], list) and definition["used_by"]
            assert definition["description"]


class TestListSecretStatus:
    def test_golden_present_and_source_reflect_real_environment(self, monkeypatch):
        monkeypatch.delenv("AZURE_KEY_VAULT_URL", raising=False)
        monkeypatch.setenv("JWT_SECRET", "test-secret-value")
        statuses = secrets_registry.list_secret_status()
        jwt = next(s for s in statuses if s["key"] == "JWT_SECRET")
        assert jwt["present"] is True
        assert jwt["source"] == "environment"

    def test_golden_missing_secret_reports_not_present_no_fake_source(self, monkeypatch):
        monkeypatch.delenv("AZURE_KEY_VAULT_URL", raising=False)
        monkeypatch.delenv("GMAIL_WATCHER_APP_PASSWORD", raising=False)
        statuses = secrets_registry.list_secret_status()
        entry = next(s for s in statuses if s["key"] == "GMAIL_WATCHER_APP_PASSWORD")
        assert entry["present"] is False
        assert entry["source"] is None

    def test_adversarial_never_returns_a_secret_value(self, monkeypatch):
        monkeypatch.delenv("AZURE_KEY_VAULT_URL", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-super-secret-value-should-never-leak")
        statuses = secrets_registry.list_secret_status()
        serialized = json.dumps(statuses)
        assert "sk-super-secret-value-should-never-leak" not in serialized


class TestUpdateSecretValue:
    def test_golden_writes_and_returns_presence_never_the_value(self, monkeypatch, tmp_path):
        import secrets_loader

        env_path = tmp_path / ".env"
        monkeypatch.setattr(secrets_loader, "_ENV_PATH", env_path)
        monkeypatch.delenv("AZURE_KEY_VAULT_URL", raising=False)
        result = secrets_registry.update_secret_value("JWT_SECRET", "super-secret-value")
        assert result["present"] is True
        assert result["source"] == "environment"
        assert "super-secret-value" not in json.dumps(result)

    def test_adversarial_unknown_key_rejected(self):
        import pytest

        with pytest.raises(ValueError):
            secrets_registry.update_secret_value("NOT_A_REAL_SECRET_KEY", "value")

    def test_adversarial_empty_value_rejected(self):
        import pytest

        with pytest.raises(ValueError):
            secrets_registry.update_secret_value("JWT_SECRET", "")


class TestSetSecretEndpoint:
    def test_golden_sets_value_and_response_never_contains_it(self, monkeypatch, tmp_path):
        import secrets_loader

        env_path = tmp_path / ".env"
        monkeypatch.setattr(secrets_loader, "_ENV_PATH", env_path)
        monkeypatch.delenv("AZURE_KEY_VAULT_URL", raising=False)
        captured = []
        monkeypatch.setattr(repositories, "append_audit_log", lambda entry: captured.append(entry))
        resp = api.set_secret_endpoint(_req(method="PUT", route_params={"key": "JWT_SECRET"}, body={"value": "super-secret-value-should-not-leak"}))
        assert resp.status_code == 200
        body_text = resp.get_body().decode()
        assert "super-secret-value-should-not-leak" not in body_text
        assert not any("super-secret-value-should-not-leak" in json.dumps(e) for e in captured)
        assert captured[0]["action"] == "secret_value_set"
        assert captured[0]["key"] == "JWT_SECRET"

    def test_adversarial_missing_value_rejected(self):
        resp = api.set_secret_endpoint(_req(method="PUT", route_params={"key": "JWT_SECRET"}, body={}))
        assert resp.status_code == 400

    def test_adversarial_unknown_key_rejected(self):
        resp = api.set_secret_endpoint(_req(method="PUT", route_params={"key": "NOT_A_REAL_SECRET_KEY"}, body={"value": "x"}))
        assert resp.status_code == 400

    def test_adversarial_recruiter_cannot_set_secrets(self, monkeypatch):
        import azure.functions as func

        monkeypatch.setattr(repositories, "get_user", lambda uid: _RECRUITER_USER if uid == "u-rec" else None)
        monkeypatch.setattr(repositories, "get_role", lambda key: _RECRUITER_ROLE_DOC if key == "recruiter" else None)
        token = auth.create_token(_RECRUITER_USER)
        resp = api.set_secret_endpoint(func.HttpRequest(
            method="PUT", url="/api/test", route_params={"key": "JWT_SECRET"},
            body=json.dumps({"value": "x"}).encode(), headers={"Authorization": f"Bearer {token}"},
        ))
        assert resp.status_code == 403


class TestListSecretsEndpoint:
    def test_golden_returns_inventory_without_values(self, monkeypatch):
        monkeypatch.delenv("AZURE_KEY_VAULT_URL", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-appear-in-response")
        resp = api.list_secrets_endpoint(_req())
        assert resp.status_code == 200
        body_text = resp.get_body().decode()
        assert "sk-should-not-appear-in-response" not in body_text
        body = json.loads(body_text)
        assert body["count"] == len(secrets_registry.SECRET_DEFINITIONS)
        assert body["vault_configured"] is False

    def test_adversarial_recruiter_cannot_view_secrets(self, monkeypatch):
        import azure.functions as func

        monkeypatch.setattr(repositories, "get_user", lambda uid: _RECRUITER_USER if uid == "u-rec" else None)
        monkeypatch.setattr(repositories, "get_role", lambda key: _RECRUITER_ROLE_DOC if key == "recruiter" else None)
        token = auth.create_token(_RECRUITER_USER)
        resp = api.list_secrets_endpoint(func.HttpRequest(
            method="GET", url="/api/test", body=None, headers={"Authorization": f"Bearer {token}"},
        ))
        assert resp.status_code == 403

    def test_adversarial_missing_token_returns_401(self):
        import azure.functions as func

        resp = api.list_secrets_endpoint(func.HttpRequest(method="GET", url="/api/test", body=None))
        assert resp.status_code == 401


class TestNewPageAndPermission:
    def test_golden_permission_exists_and_admin_has_it(self):
        admin = next(r for r in auth.DEFAULT_ROLES if r["role_key"] == "admin")
        for pid in ("secrets.read", "secrets.write"):
            assert pid in auth.PERMISSIONS
            assert pid in admin["permission_ids"]

    def test_golden_page_registered_with_correct_required_permission(self):
        page = next(p for p in auth.PAGES if p["page_id"] == "secrets_management")
        assert page["required_permission"] == "secrets.read"

    def test_golden_non_admin_roles_do_not_get_it(self):
        # azure_cloud_administrator and security_compliance_auditor
        # legitimately hold secrets.read (presence only, never values --
        # see auth.DEFAULT_ROLES); secrets.write stays admin-only.
        read_exempt = {"admin", "azure_cloud_administrator", "security_compliance_auditor"}
        write_exempt = {"admin"}
        for role in auth.DEFAULT_ROLES:
            if role["role_key"] not in read_exempt:
                assert "secrets.read" not in role["permission_ids"]
            if role["role_key"] not in write_exempt:
                assert "secrets.write" not in role["permission_ids"]
