"""Tests for llm_utils.py's Azure OpenAI provider selection, added in the
Azure real-services audit pass. These confirm: (1) the provider is chosen
by one single source of truth (_azure_openai_config()), (2) selecting
Azure actually constructs an AzureChatOpenAI/AzureOpenAI client pointed
at the configured endpoint/deployment rather than plain OpenAI, (3)
partial configuration is treated as "not configured" rather than a
half-working client, and (4) the explicit, admin-triggered real test
request never runs anything automatically and reports real failures
honestly. No real Azure OpenAI credentials exist in this environment, so
these use mocks/fakes throughout -- a real end-to-end call is BLOCKED
until AZURE_OPENAI_* is actually configured (see
docs/AZURE_REAL_SERVICES_AUDIT.md)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "semantic-library"))

import llm_utils


_AZURE_ENV = {
    "AZURE_OPENAI_ENDPOINT": "https://fake-resource.openai.azure.com/",
    "AZURE_OPENAI_API_KEY": "fake-azure-key",
    "AZURE_OPENAI_CHAT_DEPLOYMENT": "fake-chat-deployment",
}


def _set_azure_env(monkeypatch, **overrides):
    # Azure OpenAI is now the LOWEST-priority provider (Ollama > plain
    # OpenAI > Azure, see llm_utils.resolve_llm_provider()), so any test
    # that wants Azure actually selected must also clear whatever
    # higher-priority provider happens to be configured in this
    # environment (e.g. a real OPENAI_API_KEY picked up from .env).
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    for key, value in {**_AZURE_ENV, **overrides}.items():
        monkeypatch.setenv(key, value)


class TestAzureOpenAIConfig:
    def test_golden_not_configured_when_nothing_set(self, monkeypatch):
        for key in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_CHAT_DEPLOYMENT", "AZURE_OPENAI_EMBEDDING_DEPLOYMENT"):
            monkeypatch.delenv(key, raising=False)
        assert llm_utils.using_azure_openai() is False
        assert llm_utils._azure_openai_config() is None

    def test_golden_configured_when_all_three_required_fields_present(self, monkeypatch):
        _set_azure_env(monkeypatch)
        assert llm_utils.using_azure_openai() is True
        cfg = llm_utils._azure_openai_config()
        assert cfg["endpoint"] == _AZURE_ENV["AZURE_OPENAI_ENDPOINT"]
        assert cfg["chat_deployment"] == _AZURE_ENV["AZURE_OPENAI_CHAT_DEPLOYMENT"]

    def test_golden_default_api_version_used_when_unset(self, monkeypatch):
        _set_azure_env(monkeypatch)
        monkeypatch.delenv("AZURE_OPENAI_API_VERSION", raising=False)
        cfg = llm_utils._azure_openai_config()
        assert cfg["api_version"] == "2024-10-21"

    def test_adversarial_partial_configuration_is_not_configured(self, monkeypatch):
        # Endpoint + key set, but no chat deployment -- must NOT be
        # treated as "configured" (a half-working client is worse than
        # none, since it would silently fall through to the wrong
        # provider or error confusingly deep in an SDK call).
        monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", _AZURE_ENV["AZURE_OPENAI_ENDPOINT"])
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", _AZURE_ENV["AZURE_OPENAI_API_KEY"])
        monkeypatch.delenv("AZURE_OPENAI_CHAT_DEPLOYMENT", raising=False)
        assert llm_utils.using_azure_openai() is False


class TestGetChatLlm:
    def test_golden_returns_plain_chat_openai_when_not_configured(self, monkeypatch):
        for key in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_CHAT_DEPLOYMENT"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "fake-openai-key")
        llm = llm_utils.get_chat_llm(model="gpt-3.5-turbo", temperature=0.5)
        from langchain_openai import ChatOpenAI
        assert isinstance(llm, ChatOpenAI)

    def test_golden_selecting_azure_actually_constructs_an_azure_client(self, monkeypatch):
        # The core requirement: "selecting Azure actually sends requests
        # to Azure rather than the standard OpenAI endpoint" -- verified
        # by inspecting the real constructed client's own azure_endpoint/
        # deployment fields, not just its class name.
        _set_azure_env(monkeypatch)
        llm = llm_utils.get_chat_llm(model="gpt-3.5-turbo", temperature=0)
        from langchain_openai import AzureChatOpenAI
        assert isinstance(llm, AzureChatOpenAI)
        assert llm.azure_endpoint == _AZURE_ENV["AZURE_OPENAI_ENDPOINT"]
        assert llm.deployment_name == _AZURE_ENV["AZURE_OPENAI_CHAT_DEPLOYMENT"]

    def test_adversarial_never_silently_sends_azure_key_to_openai_client(self, monkeypatch):
        """If Azure is configured, the returned client must be the Azure
        one -- never a plain ChatOpenAI holding an Azure endpoint/key by
        mistake (which would send Azure credentials to api.openai.com)."""
        _set_azure_env(monkeypatch)
        llm = llm_utils.get_chat_llm()
        from langchain_openai import ChatOpenAI
        # AzureChatOpenAI subclasses ChatOpenAI in langchain_openai -- the
        # real guarantee is azure_endpoint being set, checked above; this
        # just pins that we got the Azure subclass, not a vanilla instance.
        assert type(llm).__name__ == "AzureChatOpenAI"


class TestGetEmbeddingClientAndModel:
    def test_golden_plain_openai_when_not_configured(self, monkeypatch):
        for key in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_CHAT_DEPLOYMENT"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "fake-openai-key")
        client, model = llm_utils.get_embedding_client_and_model("text-embedding-3-small")
        from openai import OpenAI
        assert isinstance(client, OpenAI)
        assert model == "text-embedding-3-small"

    def test_golden_azure_client_and_embedding_deployment_when_configured(self, monkeypatch):
        _set_azure_env(monkeypatch, AZURE_OPENAI_EMBEDDING_DEPLOYMENT="fake-embed-deployment")
        client, model = llm_utils.get_embedding_client_and_model("text-embedding-3-small")
        from openai import AzureOpenAI
        assert isinstance(client, AzureOpenAI)
        assert model == "fake-embed-deployment"  # the DEPLOYMENT name, not the model family string

    def test_adversarial_chat_configured_without_embedding_deployment_raises_clearly(self, monkeypatch):
        """Must fail loudly, not silently reuse the chat deployment for
        embeddings (a different model family) or silently fall back to
        plain OpenAI while chat already went to Azure."""
        import pytest

        _set_azure_env(monkeypatch)
        monkeypatch.delenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", raising=False)
        with pytest.raises(RuntimeError, match="AZURE_OPENAI_EMBEDDING_DEPLOYMENT"):
            llm_utils.get_embedding_client_and_model("text-embedding-3-small")


class TestOllamaProvider:
    """Ollama is the highest-priority provider: resolve_llm_provider()
    picks it over plain OpenAI or Azure OpenAI whenever OLLAMA_MODEL is
    set, per the user's explicit "prefer Ollama first, OpenAI fallback"
    choice."""

    def test_golden_not_configured_when_ollama_model_unset(self, monkeypatch):
        monkeypatch.delenv("OLLAMA_MODEL", raising=False)
        assert llm_utils._ollama_chat_config() is None
        assert llm_utils.using_ollama() is False

    def test_golden_resolve_llm_provider_prefers_ollama_over_openai_and_azure(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_MODEL", "llama3")
        monkeypatch.setenv("OPENAI_API_KEY", "fake-openai-key")
        _set_azure_env(monkeypatch)
        monkeypatch.setenv("OLLAMA_MODEL", "llama3")  # _set_azure_env clears it; re-set after
        assert llm_utils.resolve_llm_provider() == "ollama"
        assert llm_utils.using_ollama() is True
        assert llm_utils.using_azure_openai() is False

    def test_golden_resolve_llm_provider_prefers_openai_over_azure_when_no_ollama(self, monkeypatch):
        monkeypatch.delenv("OLLAMA_MODEL", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "fake-openai-key")
        assert llm_utils.resolve_llm_provider() == "openai"

    def test_golden_default_base_url_used_when_unset(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_MODEL", "llama3")
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
        cfg = llm_utils._ollama_chat_config()
        assert cfg["base_url"] == "http://localhost:11434/v1"

    def test_golden_get_chat_llm_points_at_ollama_base_url(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_MODEL", "llama3")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        for key in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_CHAT_DEPLOYMENT"):
            monkeypatch.delenv(key, raising=False)
        llm = llm_utils.get_chat_llm(temperature=0)
        assert str(llm.openai_api_base).rstrip("/") == "http://localhost:11434/v1"
        assert llm.model_name == "llama3"

    def test_golden_get_embedding_client_requires_separate_embedding_model(self, monkeypatch):
        import pytest

        monkeypatch.setenv("OLLAMA_MODEL", "llama3")
        monkeypatch.delenv("OLLAMA_EMBEDDING_MODEL", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="OLLAMA_EMBEDDING_MODEL"):
            llm_utils.get_embedding_client_and_model("text-embedding-3-small")

    def test_golden_get_embedding_client_and_model_when_configured(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_MODEL", "llama3")
        monkeypatch.setenv("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        client, model = llm_utils.get_embedding_client_and_model("text-embedding-3-small")
        from openai import OpenAI
        assert isinstance(client, OpenAI)
        assert model == "nomic-embed-text"


class TestRunAzureOpenAITestRequest:
    def test_golden_not_configured_reports_honestly(self, monkeypatch):
        for key in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_CHAT_DEPLOYMENT"):
            monkeypatch.delenv(key, raising=False)
        result = llm_utils.run_azure_openai_test_request()
        assert result["configured"] is False
        assert result["chat"] is None

    def test_golden_real_chat_and_embedding_success_reported(self, monkeypatch):
        _set_azure_env(monkeypatch, AZURE_OPENAI_EMBEDDING_DEPLOYMENT="fake-embed-deployment")

        class FakeResponse:
            content = "OK"

        class FakeLLM:
            def invoke(self, prompt):
                return FakeResponse()
        monkeypatch.setattr(llm_utils, "get_chat_llm", lambda **k: FakeLLM())

        class FakeEmbeddingData:
            embedding = [0.1, 0.2, 0.3]

        class FakeEmbeddingResponse:
            data = [FakeEmbeddingData()]

        class FakeEmbeddingsAPI:
            def create(self, model, input):
                return FakeEmbeddingResponse()

        class FakeClient:
            embeddings = FakeEmbeddingsAPI()
        monkeypatch.setattr(llm_utils, "get_embedding_client_and_model", lambda default: (FakeClient(), "fake-embed-deployment"))

        result = llm_utils.run_azure_openai_test_request()
        assert result["configured"] is True
        assert result["chat"]["outcome"] == "succeeded"
        assert result["embedding"]["outcome"] == "succeeded"
        assert result["embedding"]["dimensions"] == 3

    def test_golden_real_failure_reported_not_hidden(self, monkeypatch):
        _set_azure_env(monkeypatch)

        def raise_auth_error(**k):
            raise RuntimeError("401 Unauthorized: invalid API key")
        monkeypatch.setattr(llm_utils, "get_chat_llm", raise_auth_error)

        result = llm_utils.run_azure_openai_test_request()
        assert result["chat"]["outcome"] == "failed"
        assert "401" in result["chat"]["error"]

    def test_golden_embedding_skipped_when_no_embedding_deployment(self, monkeypatch):
        _set_azure_env(monkeypatch)
        monkeypatch.delenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", raising=False)

        class FakeLLM:
            def invoke(self, prompt):
                class R:
                    content = "OK"
                return R()
        monkeypatch.setattr(llm_utils, "get_chat_llm", lambda **k: FakeLLM())

        result = llm_utils.run_azure_openai_test_request()
        assert result["embedding"]["outcome"] == "skipped"

    def test_adversarial_never_fabricates_success_without_a_real_call(self, monkeypatch):
        """No monkeypatching of get_chat_llm/get_embedding_client_and_model
        here -- this exercises the REAL functions against fake Azure
        credentials that don't correspond to any real resource, so the
        real SDK call must fail (network/auth error), never silently
        report success."""
        _set_azure_env(monkeypatch, AZURE_OPENAI_EMBEDDING_DEPLOYMENT="fake-embed-deployment")
        result = llm_utils.run_azure_openai_test_request()
        assert result["chat"]["outcome"] == "failed"
