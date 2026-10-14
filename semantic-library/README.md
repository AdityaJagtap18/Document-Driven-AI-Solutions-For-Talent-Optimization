# Semantic Library (Vector / Embeddings Layer)

CDPI currently has **no embeddings, vector store, or semantic search anywhere** — resume-to-JD matching is done entirely by an LLM call that free-forms a "JD Match %" and keyword lists (see [`prompts/resume_merge_structured_extraction.v1.md`](../prompts/resume_merge_structured_extraction.v1.md)), and `ResumeMEtrics.py` just counts string overlaps. This folder defines the target design for a real semantic layer, to replace/augment that.

## Target architecture

**Azure AI Search** (vector + hybrid search) as the index, **Azure AI Foundry's `text-embedding-3-small`** (or `text-embedding-3-large` for higher recall) as the embedding model.

```
Resume PDF ──► chunk ──► embed (text-embedding-3-small) ──► Azure AI Search index (resume-chunks)
JD text    ──► embed (text-embedding-3-small) ──► query vector
                                                        │
                                                        ▼
                                   hybrid search (vector + keyword + semantic ranker)
                                                        │
                                                        ▼
                                   ranked resume chunks ──► grounded context for the
                                                             resume_merge_structured_extraction prompt (RAG)
```

This turns today's "ask the LLM to eyeball a JD match %" into a **retrieval-grounded** match: the LLM gets the actual highest-relevance resume chunks for the JD instead of the whole resume dumped in, improving both accuracy and token cost (Stage 11/15 of the FDE spine — RAG Evaluation, Token Economics).

## Index schema (draft)

See [`index_schema.json`](index_schema.json) — two logical indexes:
- `resume-chunks`: one document per resume chunk, vector + candidate_id + source metadata.
- `jd-corpus`: one document per job description, for cross-JD analytics (e.g. "which past JDs is this resume similar to").

## Why Azure AI Search over a standalone vector DB

- Already does hybrid (BM25 + vector) search and a built-in semantic ranker — no separate reranking step needed.
- Lives in the same Azure subscription/region as everything else in the target architecture (see the Excel tracker's `Azure_Service_Mapping` sheet) — one less vendor in the concentration-risk register (FDE spine Stage 16).
- Azure AI Foundry embedding models count against standard Azure OpenAI quota; if Azure OpenAI access isn't approved yet on the student subscription, swap in `openai`'s `text-embedding-3-small` directly (same model family, same vector dimensionality) as an interim step — the index schema doesn't change, only which service issues the embedding call.

## Status

Design only — no code implemented yet. Next step in the backlog (see Excel tracker, `Feature_Backlog` sheet, row "Semantic resume-JD matching"): build an `embed_and_index.py` that reads `Resume_Parsed_CSVs/resume_summary.csv`, chunks + embeds, and pushes to the index.
