import importlib

import secrets_loader


class TestGetSecretFallback:
    def test_golden_no_vault_configured_reads_environ(self, monkeypatch):
        monkeypatch.delenv("AZURE_KEY_VAULT_URL", raising=False)
        monkeypatch.setenv("SOME_TEST_SECRET", "value-from-env")
        assert secrets_loader.get_secret("SOME_TEST_SECRET") == "value-from-env"

    def test_edge_not_found_anywhere_returns_default(self, monkeypatch):
        monkeypatch.delenv("AZURE_KEY_VAULT_URL", raising=False)
        monkeypatch.delenv("DEFINITELY_NOT_SET_ANYWHERE", raising=False)
        assert secrets_loader.get_secret("DEFINITELY_NOT_SET_ANYWHERE", default="fallback") == "fallback"
        assert secrets_loader.get_secret("DEFINITELY_NOT_SET_ANYWHERE") is None

    def test_adversarial_vault_configured_but_lookup_fails_falls_back(self, monkeypatch):
        monkeypatch.setenv("AZURE_KEY_VAULT_URL", "https://fake-vault.vault.azure.net/")
        monkeypatch.setenv("SOME_TEST_SECRET", "value-from-env-fallback")

        def broken_client(vault_url):
            raise RuntimeError("simulated Key Vault connection failure")

        monkeypatch.setattr(secrets_loader, "_keyvault_client", broken_client)
        assert secrets_loader.get_secret("SOME_TEST_SECRET") == "value-from-env-fallback"

    def test_golden_vault_value_takes_priority_when_available(self, monkeypatch):
        monkeypatch.setenv("AZURE_KEY_VAULT_URL", "https://fake-vault.vault.azure.net/")
        monkeypatch.setenv("SOME_TEST_SECRET", "value-from-env-should-not-win")

        class FakeSecret:
            value = "value-from-keyvault"

        monkeypatch.setattr(secrets_loader, "_get_from_keyvault", lambda url, name: FakeSecret.value)
        assert secrets_loader.get_secret("SOME_TEST_SECRET") == "value-from-keyvault"

    def test_golden_name_mapping_underscores_to_hyphens(self, monkeypatch):
        captured = {}

        class FakeClient:
            def get_secret(self, name):
                captured["name"] = name

                class R:
                    value = "ok"

                return R()

        monkeypatch.setattr(secrets_loader, "_keyvault_client", lambda url: FakeClient())
        secrets_loader._get_from_keyvault("https://fake-vault.vault.azure.net/", "OPENAI_API_KEY")
        assert captured["name"] == "openai-api-key"


class TestSetEnvSecret:
    def test_golden_writes_value_and_updates_environ(self, monkeypatch, tmp_path):
        env_path = tmp_path / ".env"
        monkeypatch.setattr(secrets_loader, "_ENV_PATH", env_path)
        secrets_loader.set_env_secret("SOME_TEST_SECRET", "a-value")
        assert "a-value" in env_path.read_text()
        assert secrets_loader.get_secret("SOME_TEST_SECRET") == "a-value"

    def test_golden_overwrites_an_existing_key_not_duplicates_it(self, monkeypatch, tmp_path):
        env_path = tmp_path / ".env"
        monkeypatch.setattr(secrets_loader, "_ENV_PATH", env_path)
        secrets_loader.set_env_secret("SOME_TEST_SECRET", "first")
        secrets_loader.set_env_secret("SOME_TEST_SECRET", "second")
        contents = env_path.read_text()
        assert contents.count("SOME_TEST_SECRET") == 1
        assert secrets_loader.get_secret("SOME_TEST_SECRET") == "second"

    def test_golden_creates_the_file_if_missing(self, monkeypatch, tmp_path):
        env_path = tmp_path / "subdir_does_not_exist_yet" / ".env"
        env_path.parent.mkdir()
        monkeypatch.setattr(secrets_loader, "_ENV_PATH", env_path)
        secrets_loader.set_env_secret("SOME_TEST_SECRET", "value")
        assert env_path.exists()

    def test_adversarial_empty_name_rejected(self, monkeypatch, tmp_path):
        import pytest

        monkeypatch.setattr(secrets_loader, "_ENV_PATH", tmp_path / ".env")
        with pytest.raises(ValueError):
            secrets_loader.set_env_secret("", "value")


class TestGetSecretWithSource:
    def test_golden_environment_source_reported(self, monkeypatch):
        monkeypatch.delenv("AZURE_KEY_VAULT_URL", raising=False)
        monkeypatch.setenv("SOME_TEST_SECRET", "value-from-env")
        value, source = secrets_loader.get_secret_with_source("SOME_TEST_SECRET")
        assert value == "value-from-env"
        assert source == "environment"

    def test_golden_key_vault_source_reported(self, monkeypatch):
        monkeypatch.setenv("AZURE_KEY_VAULT_URL", "https://fake-vault.vault.azure.net/")
        monkeypatch.setattr(secrets_loader, "_get_from_keyvault", lambda url, name: "value-from-keyvault")
        value, source = secrets_loader.get_secret_with_source("SOME_TEST_SECRET")
        assert value == "value-from-keyvault"
        assert source == "key_vault"

    def test_edge_not_found_anywhere_reports_none_source(self, monkeypatch):
        monkeypatch.delenv("AZURE_KEY_VAULT_URL", raising=False)
        monkeypatch.delenv("DEFINITELY_NOT_SET_ANYWHERE", raising=False)
        value, source = secrets_loader.get_secret_with_source("DEFINITELY_NOT_SET_ANYWHERE")
        assert value is None
        assert source is None

    def test_golden_get_secret_is_defined_in_terms_of_with_source(self, monkeypatch):
        """Pins that there's exactly one resolution path -- get_secret()
        must agree with get_secret_with_source()'s value, not duplicate
        the Key Vault/environment fallback logic separately."""
        monkeypatch.delenv("AZURE_KEY_VAULT_URL", raising=False)
        monkeypatch.setenv("SOME_TEST_SECRET", "value-from-env")
        assert secrets_loader.get_secret("SOME_TEST_SECRET") == secrets_loader.get_secret_with_source("SOME_TEST_SECRET")[0]


class TestKeyVaultErrorCategorization:
    """Added in the Azure real-services audit pass: a real auth/permission
    failure must never be lumped in with the routine 'this secret just
    isn't in the vault' case -- both still fall back to .env (never
    breaks the caller), but only one is worth a loud warning and a
    recorded error an admin can see."""

    def test_golden_resource_not_found_is_not_an_error_category(self):
        from azure.core.exceptions import ResourceNotFoundError
        assert secrets_loader._categorize_keyvault_error(ResourceNotFoundError("not found")) == "secret_not_found"

    def test_golden_authentication_failure_categorized(self):
        from azure.core.exceptions import ClientAuthenticationError
        assert secrets_loader._categorize_keyvault_error(ClientAuthenticationError("bad credential")) == "authentication_failed"

    def test_golden_permission_denied_categorized(self):
        from azure.core.exceptions import HttpResponseError
        e = HttpResponseError("forbidden")
        e.status_code = 403
        assert secrets_loader._categorize_keyvault_error(e) == "authorization_denied"

    def test_golden_unknown_error_falls_back_to_generic_category(self):
        assert secrets_loader._categorize_keyvault_error(RuntimeError("something weird")) == "unknown_error"

    def test_adversarial_missing_dependency_is_its_own_distinct_category_not_hidden(self, monkeypatch):
        """The real bug this audit found: ModuleNotFoundError must be
        reported as a code defect (dependency_missing), never silently
        folded into 'secret not found' or any Azure-side category."""
        monkeypatch.setenv("AZURE_KEY_VAULT_URL", "https://fake-vault.vault.azure.net/")

        def raise_import_error(url, name):
            raise ImportError("No module named 'azure.keyvault'")
        monkeypatch.setattr(secrets_loader, "_get_from_keyvault", raise_import_error)

        value, source = secrets_loader.get_secret_with_source("SOME_TEST_SECRET", default="env-fallback-unset")
        assert secrets_loader._last_keyvault_error["https://fake-vault.vault.azure.net/"]["category"] == "dependency_missing"

    def test_golden_real_auth_failure_recorded_for_admin_visibility(self, monkeypatch):
        from azure.core.exceptions import ClientAuthenticationError
        monkeypatch.setenv("AZURE_KEY_VAULT_URL", "https://fake-vault.vault.azure.net/")
        monkeypatch.delenv("SOME_TEST_SECRET", raising=False)

        def raise_auth_error(url, name):
            raise ClientAuthenticationError("invalid credential")
        monkeypatch.setattr(secrets_loader, "_get_from_keyvault", raise_auth_error)

        secrets_loader.get_secret_with_source("SOME_TEST_SECRET")
        recorded = secrets_loader._last_keyvault_error["https://fake-vault.vault.azure.net/"]
        assert recorded["category"] == "authentication_failed"

    def test_golden_secret_not_found_falls_back_quietly_without_recording_as_loud_failure(self, monkeypatch, capsys):
        from azure.core.exceptions import ResourceNotFoundError
        monkeypatch.setenv("AZURE_KEY_VAULT_URL", "https://fake-vault.vault.azure.net/")
        monkeypatch.setenv("SOME_TEST_SECRET", "value-from-env")

        def raise_not_found(url, name):
            raise ResourceNotFoundError("secret not found")
        monkeypatch.setattr(secrets_loader, "_get_from_keyvault", raise_not_found)

        value, source = secrets_loader.get_secret_with_source("SOME_TEST_SECRET")
        assert value == "value-from-env"
        assert source == "environment"
        captured = capsys.readouterr()
        assert "[WARN]" not in captured.out  # routine case, not a warning-worthy failure


class TestGetKeyvaultStatus:
    def test_golden_not_configured_when_url_unset(self, monkeypatch):
        monkeypatch.delenv("AZURE_KEY_VAULT_URL", raising=False)
        status = secrets_loader.get_keyvault_status()
        assert status["configured"] is False
        assert status["outcome"] == "unknown"

    def test_golden_healthy_on_successful_live_call(self, monkeypatch):
        monkeypatch.setenv("AZURE_KEY_VAULT_URL", "https://fake-vault.vault.azure.net/")

        class FakeClient:
            def list_properties_of_secrets(self, max_page_size=None):
                return iter([])
        monkeypatch.setattr(secrets_loader, "_keyvault_client", lambda url: FakeClient())

        status = secrets_loader.get_keyvault_status()
        assert status["configured"] is True
        assert status["outcome"] == "healthy"

    def test_golden_unhealthy_with_real_category_on_failure(self, monkeypatch):
        from azure.core.exceptions import ClientAuthenticationError
        monkeypatch.setenv("AZURE_KEY_VAULT_URL", "https://fake-vault.vault.azure.net/")

        def broken_client(url):
            raise ClientAuthenticationError("bad credential")
        monkeypatch.setattr(secrets_loader, "_keyvault_client", broken_client)

        status = secrets_loader.get_keyvault_status()
        assert status["configured"] is True
        assert status["outcome"] == "unhealthy"
        assert status["error_category"] == "authentication_failed"

    def test_adversarial_never_reports_healthy_without_a_real_call_succeeding(self, monkeypatch):
        monkeypatch.setenv("AZURE_KEY_VAULT_URL", "https://fake-vault.vault.azure.net/")

        def broken_client(url):
            raise RuntimeError("network unreachable")
        monkeypatch.setattr(secrets_loader, "_keyvault_client", broken_client)

        status = secrets_loader.get_keyvault_status()
        assert status["outcome"] != "healthy"
