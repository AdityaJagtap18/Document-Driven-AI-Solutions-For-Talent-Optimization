"""Embedding calls. Uses Azure OpenAI's embeddings endpoint when
AZURE_OPENAI_ENDPOINT/AZURE_OPENAI_API_KEY/AZURE_OPENAI_CHAT_DEPLOYMENT/
AZURE_OPENAI_EMBEDDING_DEPLOYMENT are all configured (see
llm_utils.get_embedding_client_and_model()), otherwise the plain OpenAI
embeddings API -- added in the Azure real-services audit pass; previously
always called plain OpenAI regardless of Azure configuration, which this
module's own prior docstring flagged honestly as "Azure OpenAI/Foundry
access isn't provisioned yet." Vector dimensionality is unchanged either
way (both serve the same text-embedding-3-small model family, assuming
the Azure deployment is also that model -- an admin's responsibility
when choosing what to deploy)."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from secrets_loader import get_secret  # noqa: E402
from observability import trace_stage  # noqa: E402
from model_pricing import estimate_embedding_cost  # noqa: E402
import llm_utils  # noqa: E402

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536


def _require_key():
    if llm_utils.using_azure_openai():
        return  # Azure OpenAI uses AZURE_OPENAI_API_KEY, checked by get_embedding_client_and_model() itself
    key = get_secret("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY not set. Add it to your .env file (see .env.example), configure AZURE_KEY_VAULT_URL, or set AZURE_OPENAI_ENDPOINT/AZURE_OPENAI_API_KEY/AZURE_OPENAI_CHAT_DEPLOYMENT/AZURE_OPENAI_EMBEDDING_DEPLOYMENT to use Azure OpenAI instead.")
    os.environ["OPENAI_API_KEY"] = key  # OpenAI() client reads this from the environment implicitly


def embed_text(text: str, trace_id: str = None, correlation_id: str = None, workflow: str = "resume_matching") -> list:
    """Embeds a single string. Raises ValueError on empty input."""
    text = (text or "").replace("\n", " ").strip()
    if not text:
        raise ValueError("Cannot embed empty text.")
    return embed_texts([text], trace_id=trace_id, correlation_id=correlation_id, workflow=workflow)[0]


def embed_texts(texts: list, batch_size: int = 100, trace_id: str = None, correlation_id: str = None,
                 workflow: str = "resume_matching") -> list:
    """Embeds a list of strings, batching requests. Empty strings are rejected.
    Traced as an "embedding_generation" AI Observability stage (see
    observability.trace_stage) -- this was a completely unobserved pipeline
    stage before the AI Observability Center feature; only LLM chat calls
    had any instrumentation. Also records real token usage (from OpenAI's
    own response) and an estimated cost via model_pricing.py's documented,
    versioned pricing table -- embeddings aren't covered by langchain's
    get_openai_callback() the way chat completions are, so this was a real
    cost-tracking gap before this feature, not merely an unobserved stage."""
    span_metadata = {"model": EMBEDDING_MODEL, "input_count": len(texts)}
    with trace_stage(
        workflow, "embedding_generation", trace_id=trace_id, correlation_id=correlation_id,
        metadata=span_metadata,
    ):
        _require_key()

        cleaned = []
        for t in texts:
            t = (t or "").replace("\n", " ").strip()
            if not t:
                raise ValueError("Cannot embed an empty string (found in the input list).")
            cleaned.append(t)

        client, model = llm_utils.get_embedding_client_and_model(EMBEDDING_MODEL)
        vectors = []
        total_tokens = 0
        for i in range(0, len(cleaned), batch_size):
            batch = cleaned[i : i + batch_size]
            resp = client.embeddings.create(model=model, input=batch)
            vectors.extend([d.embedding for d in resp.data])
            total_tokens += getattr(resp.usage, "total_tokens", 0) or 0

        # Mutates the SAME dict trace_stage holds a reference to, so its
        # own automatic exit-time record_ai_trace() call (not a second,
        # duplicate one here) picks up the final cost/token fields --
        # avoids writing two records for one span.
        cost_usd, cost_note = estimate_embedding_cost(EMBEDDING_MODEL, total_tokens)
        span_metadata["total_tokens"] = total_tokens
        span_metadata["cost_usd"] = cost_usd
        span_metadata["cost_source"] = cost_note if cost_usd is not None else None
        span_metadata["cost_unavailable_reason"] = None if cost_usd is not None else cost_note

        return vectors
