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
