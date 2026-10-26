import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "context-lake"))

import model_routing  # noqa: E402
import repositories  # noqa: E402


class TestValidateRoutingConfig:
    def test_golden_valid_config_is_valid(self):
        config = {"chat_candidate_qa": "gpt-4o-mini", "jd_generation": "gpt-4o"}
        assert model_routing.validate_routing_config(config) == []

    def test_golden_empty_config_is_valid(self):
        assert model_routing.validate_routing_config({}) == []

    def test_adversarial_not_a_dict_is_rejected(self):
        assert model_routing.validate_routing_config(None) != []
        assert model_routing.validate_routing_config("not a dict") != []

    def test_adversarial_unroutable_label_is_rejected(self):
        errors = model_routing.validate_routing_config({"not_a_real_label": "gpt-4o"})
        assert len(errors) == 1
        assert "not_a_real_label" in errors[0]

    def test_adversarial_empty_model_string_is_rejected(self):
        errors = model_routing.validate_routing_config({"chat_candidate_qa": ""})
        assert len(errors) == 1

    def test_adversarial_whitespace_only_model_is_rejected(self):
        errors = model_routing.validate_routing_config({"chat_candidate_qa": "   "})
        assert len(errors) == 1

    def test_adversarial_non_string_model_is_rejected(self):
        errors = model_routing.validate_routing_config({"chat_candidate_qa": 123})
        assert len(errors) == 1

    def test_adversarial_multiple_errors_all_reported(self):
        errors = model_routing.validate_routing_config({"bad_label": "gpt-4o", "chat_candidate_qa": ""})
        assert len(errors) == 2

    def test_golden_all_routable_labels_covered_resume_steps_excluded(self):
        # resume_chunk_summarization/resume_merge_structured_extraction are
        # deliberately NOT routable here -- they already have a real config
        # surface via rag_config.py.
        assert "resume_chunk_summarization" not in model_routing.ROUTABLE_LABELS
        assert "resume_merge_structured_extraction" not in model_routing.ROUTABLE_LABELS
        errors = model_routing.validate_routing_config({"resume_chunk_summarization": "gpt-4o"})
        assert len(errors) == 1


class TestResolveModel:
    def test_golden_configured_override_is_used(self):
        model, source = model_routing.resolve_model("chat_candidate_qa", {"chat_candidate_qa": "gpt-4o-mini"})
        assert model == "gpt-4o-mini"
        assert source == "configured_override"

    def test_golden_no_override_falls_back_to_default(self):
        model, source = model_routing.resolve_model("chat_candidate_qa", {})
        assert model == model_routing.DEFAULT_MODELS["chat_candidate_qa"]
        assert source == "default"

    def test_edge_none_config_falls_back_to_default(self):
        model, source = model_routing.resolve_model("chat_candidate_qa", None)
        assert model == model_routing.DEFAULT_MODELS["chat_candidate_qa"]
        assert source == "default"

    def test_adversarial_empty_string_override_falls_back_to_default(self):
        model, source = model_routing.resolve_model("chat_candidate_qa", {"chat_candidate_qa": ""})
        assert source == "default"

    def test_adversarial_non_string_override_falls_back_to_default(self):
        model, source = model_routing.resolve_model("chat_candidate_qa", {"chat_candidate_qa": 123})
        assert source == "default"

    def test_edge_unroutable_label_still_resolves_to_something_real(self):
        model, source = model_routing.resolve_model("totally_unknown_label", {})
        assert model == "gpt-3.5-turbo"
        assert source == "default"

    def test_golden_every_default_matches_the_real_hardcoded_value(self):
        # Confirms DEFAULT_MODELS actually matches what every live call
        # site was hardcoded to before this feature existed -- a change
        # here would silently alter production behavior for an
        # unconfigured label.
        for label in model_routing.ROUTABLE_LABELS:
            assert model_routing.DEFAULT_MODELS[label] == "gpt-3.5-turbo"


class TestGetRuntimeRoutingConfig:
    def test_golden_returns_active_routes(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_model_routing_config", lambda: {"routes": {"chat_candidate_qa": "gpt-4o"}})
        assert model_routing.get_runtime_routing_config() == {"chat_candidate_qa": "gpt-4o"}

    def test_edge_no_config_saved_yet_returns_empty_dict(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_model_routing_config", lambda: None)
        assert model_routing.get_runtime_routing_config() == {}

    def test_adversarial_cosmos_failure_falls_back_to_empty_dict_not_a_crash(self, monkeypatch):
        def broken():
            raise RuntimeError("Cosmos unavailable")

        monkeypatch.setattr(repositories, "get_model_routing_config", broken)
        assert model_routing.get_runtime_routing_config() == {}
