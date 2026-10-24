"""Versioned model pricing config -- the AI Observability Center's "cost
monitoring" needs a documented source for every cost figure it shows, not
an invented number.

Two real cost sources exist in this repo after this feature:
  1. LLM chat calls (ChatOpenAI.predict()) already get a REAL computed
     cost from langchain_community.callbacks.get_openai_callback()
     (llm_utils.py) -- that callback has its own internal, maintained
     OpenAI pricing table. record_llm_call() tags these
     cost_source="langchain_openai_callback" (see observability.py).
  2. Embedding calls (semantic-library/embeddings.py's embed_texts())
     had NO cost tracking at all before this feature -- OpenAI's
     embeddings.create() isn't wrapped by get_openai_callback() the way
     chat completions are. THIS module's PRICING table + estimate_cost()
     fills that specific, real gap; callers tag the result
     cost_source="documented_estimate" so a viewer always knows which of
     the two calculation methods produced a given number.

Prices are MANUALLY ENTERED published list prices, not a live pricing
feed -- see PRICING_SOURCE/PRICING_LAST_UPDATED below. Never claim this
is a billed amount; it's an estimate from a documented, versioned table.
"""

PRICING_VERSION = "2026-10-20.1"
PRICING_SOURCE = "OpenAI published list pricing, manually entered -- not a live pricing API."
PRICING_LAST_UPDATED = "2026-10-20"

# USD per 1,000 tokens. Embedding models are input-only (no output/
# completion tokens); chat models aren't priced here at all -- their real
# cost already comes from langchain's own callback (see module docstring).
EMBEDDING_PRICING_PER_1K_TOKENS = {
    "text-embedding-3-small": 0.00002,
    "text-embedding-3-large": 0.00013,
}


def estimate_embedding_cost(model: str, total_tokens: int):
    """Returns (cost_usd, source_label) or (None, reason) if this model
    isn't in the documented table -- NEVER guesses a price for an
    unlisted model. total_tokens may be None (unknown usage) -- also
    returns (None, reason) in that case, never a fabricated number."""
    if total_tokens is None:
        return None, "Token usage unavailable for this call."
    price = EMBEDDING_PRICING_PER_1K_TOKENS.get(model)
    if price is None:
        return None, f"No documented price for model '{model}' (pricing table version {PRICING_VERSION})."
    return round((total_tokens / 1000.0) * price, 8), "documented_estimate"
