"""Real retrieval (and optional generation) preview for the RAG
Configuration Console -- runs against the ACTUAL on-disk semantic index
(semantic-library/index/resume-chunks.json), the same LocalVectorStore
every real resume-parsing run reads/writes. Never simulates retrieval
results or fabricates a response.

Distinguishes four real facts the spec asks never be collapsed into one
status, via get_source_status():
  - "configured": an index PATH is defined (always true -- there's only
    one knowledge source in this repo)
  - "connected": the index FILE exists and loads without error
  - "indexed": the loaded index has at least one chunk
  - "retrievable": a real probe query against it returns at least one
    result (proves cosine search actually works, not just that the file
    parses)
"""

import sys
import time
from pathlib import Path

_SEMANTIC_LIB_DIR = Path(__file__).resolve().parent / "semantic-library"
sys.path.insert(0, str(_SEMANTIC_LIB_DIR))
SOURCE_INDEX_PATH = _SEMANTIC_LIB_DIR / "index" / "resume-chunks.json"

# Generation preview is real (genuine LLM calls, real cost) but capped --
# running every top-K chunk through two LLM calls each on every preview
# click would be an unbounded cost/latency surface for an admin config
# page. 3 matches this repo's own "cheap enough to always do" intuition
# without being so small the preview feels fake.
MAX_GENERATION_PREVIEW_CHUNKS = 3


def get_source_status() -> dict:
    status = {"configured": True, "connected": False, "indexed": False, "retrievable": False, "chunk_count": 0, "error": None}
    if not SOURCE_INDEX_PATH.exists():
        status["error"] = "No index file found -- run semantic-library/build_index.py or process at least one resume first."
        return status
    try:
        from vector_store import LocalVectorStore

        store = LocalVectorStore(str(SOURCE_INDEX_PATH))
    except Exception as e:
        status["error"] = f"Index file exists but failed to load: {e}"
        return status
    status["connected"] = True
    status["chunk_count"] = len(store)
    status["indexed"] = status["chunk_count"] > 0
    if status["indexed"]:
        try:
            from embeddings import embed_text

            probe_vector = embed_text("software engineer")
            status["retrievable"] = len(store.search(probe_vector, top_k=1)) > 0
        except Exception as e:
            status["error"] = f"Index has {status['chunk_count']} chunk(s) but a probe query failed: {e}"
    return status


def run_retrieval_preview(jd_text: str, top_k: int, similarity_threshold: float) -> dict:
    """Real retrieval against the real index. Returns {chunks, latency_ms,
    total_candidates, insufficient_evidence}. `chunks` is every result
    that passed similarity_threshold (never more than top_k) -- if
    threshold filters everything out, insufficient_evidence is True and
    chunks is [], which the UI must show as a real "no sufficiently
    relevant evidence" state, not an empty-looking error."""
    from embeddings import embed_text
    from vector_store import LocalVectorStore

    start = time.perf_counter()
    if not SOURCE_INDEX_PATH.exists():
        raise FileNotFoundError(f"No index at {SOURCE_INDEX_PATH} -- run build_index.py or process a resume first.")
    store = LocalVectorStore(str(SOURCE_INDEX_PATH))
    query_vector = embed_text(jd_text)
    # Fetch a wider pool than top_k so threshold-filtering has real candidates to drop.
    raw_results = store.search(query_vector, top_k=max(top_k * 4, top_k))
    passed = [r for r in raw_results if r["score"] >= similarity_threshold][:top_k]
    latency_ms = round((time.perf_counter() - start) * 1000, 1)
    return {
        "chunks": passed, "latency_ms": latency_ms, "total_candidates": len(raw_results),
        "insufficient_evidence": len(passed) == 0,
    }


def run_generation_preview(jd_text: str, chunks: list, chunk_prompt: dict, merge_prompt: dict, generation_config: dict) -> dict:
    """Real LLM calls (chunk-summarization then merge), capped at
    MAX_GENERATION_PREVIEW_CHUNKS -- genuinely exercises the active/draft
    prompt templates and generation parameters, not a canned response.
    `citations` lists exactly which retrieved chunks fed the final
    output (resume_name + chunk id + score), so the preview UI can show
    real grounding, not an assumed one."""
    from langchain_community.callbacks import get_openai_callback

    from llm_utils import get_chat_llm
    from rag_config import sanitize_for_prompt

    start = time.perf_counter()
    used_chunks = chunks[:MAX_GENERATION_PREVIEW_CHUNKS]
    truncated_note = None
    if len(chunks) > MAX_GENERATION_PREVIEW_CHUNKS:
        truncated_note = f"Preview generation is capped at {MAX_GENERATION_PREVIEW_CHUNKS} chunks (out of {len(chunks)} retrieved) to bound cost/latency."

    # jd_text and every chunk's text are UNTRUSTED input -- same
    # sanitization ResumeParser.py's real pipeline applies (see
    # rag_config.py's docstring), so a preview run exercises the same
    # defense an admin is actually relying on in production.
    sanitized_jd = sanitize_for_prompt(jd_text)

    cs_cfg = generation_config["chunk_summarization"]
    llm_chunk = get_chat_llm(temperature=cs_cfg["temperature"], max_tokens=cs_cfg["max_tokens"], model=cs_cfg["model"])
    summaries = []
    total_tokens = 0
    with get_openai_callback() as cb:
        for c in used_chunks:
            chunk_text = c["metadata"].get("chunk_text", "")
            prompt = chunk_prompt["template"].format(text=sanitize_for_prompt(chunk_text), jd=sanitized_jd)
            summaries.append(llm_chunk.predict(prompt))

        merge_cfg = generation_config["merge_extraction"]
        llm_merge = get_chat_llm(
            temperature=merge_cfg["temperature"], max_tokens=merge_cfg["max_tokens"], model=merge_cfg["model"],
            model_kwargs={"response_format": {"type": "json_object"}},
        )
        merge_text = merge_prompt["template"].format(summaries=sanitize_for_prompt("".join(summaries)), jd=sanitized_jd)
        final_response = llm_merge.predict(merge_text)
        total_tokens = cb.total_tokens

    latency_ms = round((time.perf_counter() - start) * 1000, 1)
    citations = [
        {"resume_name": c["metadata"].get("resume_name"), "chunk_index": c["metadata"].get("chunk_index"), "score": c["score"]}
        for c in used_chunks
    ]
    return {
        "chunk_summaries": summaries, "final_response": final_response, "citations": citations,
        "latency_ms": latency_ms, "total_tokens": total_tokens, "truncated_note": truncated_note,
    }
