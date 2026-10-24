"""Shared "embed one resume and index it" helper, used by ResumeParser.py
(incremental, per-resume, at parse time). build_index.py's own bulk path
stays separate on purpose — it batches the embedding API call across every
resume at once for an efficient full rebuild, whereas ResumeParser.py
processes one resume at a time anyway (interleaved with other per-resume LLM
calls), so a per-resume embedding call costs nothing extra there."""

import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chunking import chunk_resume_text  # noqa: E402
from embeddings import embed_texts  # noqa: E402
from vector_store import LocalVectorStore, cosine_scores  # noqa: E402
from observability import trace_stage  # noqa: E402


def embed_and_index_resume_text(resume_name: str, resume_text: str, store: LocalVectorStore,
                                 trace_id: str = None, correlation_id: str = None,
                                 workflow: str = "resume_parsing", max_chars: int = 1500) -> list:
    """Chunks (section-aware) + embeds one resume's already-extracted text,
    adds every chunk to `store` (NOT saved to disk — call store.save()
    yourself once you're done), and returns
    [{"id", "text", "vector", "metadata"}, ...] in original chunk order, so
    the caller can also rank/filter them (e.g. against a JD) without a
    second embedding call.

    trace_id/correlation_id (see observability.py's trace schema), if
    given, thread through to BOTH the chunking span this function records
    directly and the embedding_generation span embed_texts() records
    itself -- so a caller like ResumeParser.py gets two real, separate,
    correctly-ordered stages in one resume's trace instead of one opaque
    fused span. `workflow` is threaded the same way so every span sharing
    one trace_id reports the SAME workflow name (a trace-level concept,
    not a per-span one) -- defaults to "resume_parsing" since that's this
    function's only real caller today. `max_chars` is the RAG
    Configuration Console's chunking.max_chars knob (rag_config.py) --
    defaults to the value this repo has always hardcoded, so a caller
    that doesn't pass it gets identical behavior to before this
    parameter existed."""
    with trace_stage(workflow, "chunking", trace_id=trace_id, correlation_id=correlation_id):
        chunks = chunk_resume_text(resume_text, max_chars=max_chars)
    if not chunks:
        return []
    vectors = embed_texts(chunks, trace_id=trace_id, correlation_id=correlation_id, workflow=workflow)
    results = []
    for i, (chunk, vector) in enumerate(zip(chunks, vectors)):
        doc_id = f"{resume_name}::chunk{i}"
        metadata = {"resume_name": resume_name, "chunk_index": i, "chunk_text": chunk[:500]}
        store.add(doc_id, vector, metadata)
        results.append({"id": doc_id, "text": chunk, "vector": vector, "metadata": metadata})
    return results


def rank_by_relevance(results: list, query_vector, top_k: int, similarity_threshold: float = 0.0) -> list:
    """Ranks embed_and_index_resume_text()'s output by cosine similarity to
    query_vector (e.g. a JD embedding), drops anything below
    similarity_threshold (the RAG Configuration Console's
    retrieval.similarity_threshold -- defaults to 0.0, which drops
    nothing, so a caller that doesn't pass it gets identical behavior to
    before this parameter existed), then returns the top_k survivors,
    re-sorted back into original chunk order so downstream summarization
    still reads the resume top-to-bottom, not by relevance rank. Can
    return fewer than top_k (or zero) results if the threshold filters
    some/all candidates out -- callers must handle an empty result as
    "insufficient relevant evidence," not an error."""
    if not results:
        return results
    vectors = [r["vector"] for r in results]
    scores = cosine_scores(vectors, query_vector)
    candidate_idx = [i for i in range(len(results)) if scores[i] >= similarity_threshold]
    ranked_idx = sorted(candidate_idx, key=lambda i: -scores[i])[:top_k]
    ranked_idx.sort()
    return [results[i] for i in ranked_idx]
