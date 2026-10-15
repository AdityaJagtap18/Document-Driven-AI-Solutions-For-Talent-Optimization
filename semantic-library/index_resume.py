"""Shared "embed one resume and index it" helper, used by ResumeParser.py
(incremental, per-resume, at parse time). build_index.py's own bulk path
stays separate on purpose — it batches the embedding API call across every
resume at once for an efficient full rebuild, whereas ResumeParser.py
processes one resume at a time anyway (interleaved with other per-resume LLM
calls), so a per-resume embedding call costs nothing extra there."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from chunking import chunk_resume_text  # noqa: E402
from embeddings import embed_texts  # noqa: E402
from vector_store import LocalVectorStore, cosine_scores  # noqa: E402


def embed_and_index_resume_text(resume_name: str, resume_text: str, store: LocalVectorStore) -> list:
    """Chunks (section-aware) + embeds one resume's already-extracted text,
    adds every chunk to `store` (NOT saved to disk — call store.save()
    yourself once you're done), and returns
    [{"id", "text", "vector", "metadata"}, ...] in original chunk order, so
    the caller can also rank/filter them (e.g. against a JD) without a
    second embedding call."""
    chunks = chunk_resume_text(resume_text)
    if not chunks:
        return []
    vectors = embed_texts(chunks)
    results = []
    for i, (chunk, vector) in enumerate(zip(chunks, vectors)):
        doc_id = f"{resume_name}::chunk{i}"
        metadata = {"resume_name": resume_name, "chunk_index": i, "chunk_text": chunk[:500]}
        store.add(doc_id, vector, metadata)
        results.append({"id": doc_id, "text": chunk, "vector": vector, "metadata": metadata})
    return results


def rank_by_relevance(results: list, query_vector, top_k: int) -> list:
    """Ranks embed_and_index_resume_text()'s output by cosine similarity to
    query_vector (e.g. a JD embedding) and returns the top_k, re-sorted back
    into original chunk order — so downstream summarization still reads the
    resume top-to-bottom, not by relevance rank. Returns everything unranked
    if top_k >= len(results) (nothing to filter)."""
    if not results or top_k >= len(results):
        return results
    vectors = [r["vector"] for r in results]
    scores = cosine_scores(vectors, query_vector)
    ranked_idx = sorted(range(len(results)), key=lambda i: -scores[i])[:top_k]
    ranked_idx.sort()
    return [results[i] for i in ranked_idx]
