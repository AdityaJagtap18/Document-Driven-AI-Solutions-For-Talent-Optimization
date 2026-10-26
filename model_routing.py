"""F-17 (intelligent AI model routing and optimization) -- minimal slice.

Scope note (read before extending this file): the full F-17 feature
prompt describes multi-provider routing with capability/cost/latency-
based selection, configurable fallback chains, quality-triggered
escalation to a stronger model, and spending limits. This repository has
exactly ONE real, verified LLM provider path per deployment (Azure
OpenAI when configured, else plain OpenAI -- see llm_utils.get_chat_llm()
and using_azure_openai()), with a single chat deployment/model string at
a time. There is no second real, independently-verified model available
in this environment to route "simple" vs. "complex" tasks between, and
inventing one would be fabricating a capability this repo doesn't have.

What IS real and genuinely missing (confirmed by inspecting every real
LLM call site, not assumed): two of this repo's AI pipeline steps
(resume_chunk_summarization, resume_merge_structured_extraction) already
have a real, admin-configurable per-step model field via the RAG
Configuration Console (rag_config.py) -- NOT duplicated here. But six
other real, live LLM call sites have their model HARDCODED to
"gpt-3.5-turbo" with no admin control at all: chat_candidate_qa,
interview_questions_generation, interview_bot_answer_scoring,
rag_groundedness_judge, ai_match_quality_judge, jd_generation (and its
sibling jd_document_extraction). See ROUTABLE_LABELS.

This slice closes exactly that gap: a real, centralized, admin-
configurable model-override registry for those six call sites, with
DEFAULT_MODELS preserving today's exact hardcoded behavior when nothing
is configured (zero regression).

REAL, CONFIRMED LIMITATION on one label: interview_bot_answer_scoring
(interview_bot.py) is included in ROUTABLE_LABELS and its default is
preserved, but it is NOT wired to actually consult this config in this
slice -- confirmed by import-graph inspection that interview_bot.py is
a standalone CLI script (uses input(), builds its LLM client at module-
import time) with no live HTTP call site importing it anywhere in this
repo. Restructuring that module's import-time side effect was judged
out of scope for a feature slice whose real, live-traffic value is the
5 other labels, which ARE wired. The label stays in the registry (an
admin can still configure it) so this is forward-compatible, not
silently dropped. This is deliberately a SELECTION
mechanism (which already-approved model string a given task uses), not a
routing ENGINE that compares providers/cost/latency live and chooses
automatically -- that would require at least two real, independently
verified models to choose between, which this environment does not have.
Documented as a real limitation, not hidden.

Deliberately deferred (see docs/fde/IMPLEMENTATION_STATUS.md): multi-
provider routing, automatic complexity-based selection, quality-
triggered escalation to a stronger model, per-feature spending limits,
context-length/capability validation. Retry/backoff on failure already
exists (llm_utils.call_with_retry) and is reused unchanged -- not
rebuilt here.
"""

# The real, hardcoded-with-no-admin-control LLM call sites this slice
# gives a configurable override to -- confirmed by direct inspection of
# azure-functions/api_blueprint.py, interview_bot.py, and
# jd_management.py. resume_chunk_summarization/resume_merge_structured_
# extraction are deliberately excluded -- they already have a real
# admin-configurable model field via rag_config.py's RAG Configuration
# Console; adding a second, competing config surface for the same two
# steps would confuse which one is authoritative.
ROUTABLE_LABELS = {
    "chat_candidate_qa",
    "interview_questions_generation",
    "interview_bot_answer_scoring",
    "rag_groundedness_judge",
    "ai_match_quality_judge",
    "jd_generation",
    "jd_document_extraction",
}

# Today's real hardcoded values at each call site -- the exact fallback
# behavior when no override is configured, so this feature changes
# nothing by default.
DEFAULT_MODELS = {label: "gpt-3.5-turbo" for label in ROUTABLE_LABELS}


def validate_routing_config(config: dict) -> list:
    """Returns a list of human-readable validation errors -- empty list
    means valid. Every key must be one of the real ROUTABLE_LABELS (a
    typo'd or made-up label would silently never apply); every value
    must be a non-empty model identifier string."""
    errors = []
    if not isinstance(config, dict):
        return ["Request body must be a JSON object mapping label -> model."]
    for label, model in config.items():
        if label not in ROUTABLE_LABELS:
            errors.append(f"'{label}' is not a routable label. Routable labels: {sorted(ROUTABLE_LABELS)}.")
            continue
        if not isinstance(model, str) or not model.strip():
            errors.append(f"Model for '{label}' must be a non-empty string, got {model!r}.")
    return errors


def resolve_model(label: str, config: dict) -> tuple:
    """Returns (model, source) -- source is "configured_override" when
    an admin-set value applies, or "default" when falling back to
    DEFAULT_MODELS (today's real hardcoded value). Never raises: an
    unroutable label or a missing/empty config entry always resolves to
    a real, valid default rather than None."""
    config = config or {}
    override = config.get(label)
    if isinstance(override, str) and override.strip():
        return override, "configured_override"
    return DEFAULT_MODELS.get(label, "gpt-3.5-turbo"), "default"


def get_runtime_routing_config() -> dict:
    """The active routing config, falling back to {} (meaning "use every
    label's default") on any failure -- Cosmos unavailable, nothing ever
    configured. Mirrors rag_config.get_runtime_config()'s exact fallback
    discipline, so a Cosmos outage costs one fallback decision per call,
    never breaks the real LLM call itself."""
    try:
        import repositories

        doc = repositories.get_model_routing_config()
        return (doc or {}).get("routes", {})
    except Exception:
        return {}
