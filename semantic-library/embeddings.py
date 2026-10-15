"""Embedding calls. Uses the OpenAI embeddings API directly (not the Azure AI
Foundry endpoint from the design doc) since Azure OpenAI/Foundry access isn't
provisioned yet — same interim fallback already noted in README.md and the
Azure_Service_Mapping sheet of the tracker. Swapping to Azure AI Foundry later
only changes the client construction below, not the vector dimensionality
(both serve the same text-embedding-3-small model family)."""

import os

from dotenv import load_dotenv

load_dotenv()

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536


def _require_key():
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY not set. Add it to your .env file (see .env.example).")


def embed_text(text: str) -> list:
    """Embeds a single string. Raises ValueError on empty input."""
    text = (text or "").replace("\n", " ").strip()
    if not text:
        raise ValueError("Cannot embed empty text.")
    return embed_texts([text])[0]


def embed_texts(texts: list, batch_size: int = 100) -> list:
    """Embeds a list of strings, batching requests. Empty strings are rejected."""
    _require_key()
    from openai import OpenAI

    cleaned = []
    for t in texts:
        t = (t or "").replace("\n", " ").strip()
        if not t:
            raise ValueError("Cannot embed an empty string (found in the input list).")
        cleaned.append(t)

    client = OpenAI()
    vectors = []
    for i in range(0, len(cleaned), batch_size):
        batch = cleaned[i : i + batch_size]
        resp = client.embeddings.create(model=EMBEDDING_MODEL, input=batch)
        vectors.extend([d.embedding for d in resp.data])
    return vectors
