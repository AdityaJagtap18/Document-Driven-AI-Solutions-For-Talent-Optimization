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

## Code

- [`embeddings.py`](embeddings.py) — calls the real OpenAI embeddings API (`text-embedding-3-small`, 1536 dims) directly, since Azure OpenAI/Foundry access isn't provisioned yet. Reads `OPENAI_API_KEY` from `.env`.
- [`vector_store.py`](vector_store.py) — one `VectorStore` interface, two implementations:
  - `LocalVectorStore`: a JSON-backed numpy cosine-similarity index. No external service required — this is what actually runs today.
  - `AzureAISearchVectorStore`: the target production backend from the design above. **Not yet tested** — no Azure AI Search resource is provisioned. Reads `AZURE_SEARCH_ENDPOINT`/`AZURE_SEARCH_KEY` from `.env` once it exists.
- [`chunking.py`](chunking.py) — `chunk_resume_text()`: section-aware chunking (splits on `EDUCATION`/`EXPERIENCE`/`SKILLS`/`PROJECTS`/etc. headers, strips contact-info noise from the lead block). `chunk_text()`, the original naive char-based chunker (same approach as `ResumeParser.py`'s `chunk_text()`), is kept as the fallback when no recognizable section headers are found.
- [`build_index.py`](build_index.py) — extracts text from every PDF in `OutputResume_Folder/`, chunks (via `chunk_resume_text`), embeds, and writes `index/resume-chunks.json`; also embeds every `jd_folder/*.txt` into `index/jd-corpus.json`. `index/` is gitignored — it's resume text + embeddings (PII), and it's cheaply rebuildable.
- [`search.py`](search.py) — `search_resumes_for_jd()` / `best_matching_resumes()`, plus a CLI that prints ranked matches for the most recent JD in `jd_folder/`.

## Verified against real data

**First run** (naive char-based `chunk_text`, 90 chunks from 81 PDFs): worked end-to-end, but match quality was weak — top scores 0.39–0.49 cosine similarity, and the top-ranked candidates for the Machine Learning Developer JD were mostly SQL/database profiles. Root cause: chunk 0 of nearly every resume was dominated by name/contact-info/education header text, carrying almost no skill-relevant signal.

**After switching to `chunk_resume_text`** (section-aware splitting, 187 chunks from the same 81 PDFs — more, smaller, section-focused): re-ran the same JD query. Top scores rose to **0.55–0.60**, and every one of the top 8 matches is now a `Skills:` chunk that **literally contains "Machine Learning"** — e.g. `Yuvraj_Raval_resume.pdf` (0.5972), `Zaina_Behl_resume.pdf` (0.5913), `Saira_Amble_resume.pdf` (0.5818). This is the correct, explainable behavior for an ML Developer JD, verified by actually running it, not assumed.

Chunks are now labeled by section (`"Education: ..."`, `"Experience: ..."`, `"Technical Skills: ..."`), which also makes `chunk_text` previews in search results self-explanatory instead of opaque fragments.

## Status

Local backend (`LocalVectorStore`) is built, dependency-free, and verified end-to-end against real resumes and a real JD — including the chunking-quality fix above, measured before and after. `AzureAISearchVectorStore` is written but untested (no Azure AI Search resource yet). Not wired into `ResumeParser.py`/`ResumeMEtrics.py` — standalone for now, per the user.
