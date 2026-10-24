# RAG Configuration Console

A real, backend-connected configuration console for the resume-matching
RAG pipeline (`/admin/rag-config`), extending the Draft → Validate →
Activate → Rollback lifecycle already built for the Azure Service Hub.

## Architecture inventory (verified against the repository)

This repo's RAG use case is **JD-to-resume-chunk matching for recruiter
summarization**, not an interactive document-QA chatbot. That distinction
shapes the whole console — several spec requirements (citation format for
a chat "response," evaluation against a "test query" with labeled
relevance) assume the latter and don't map cleanly onto this pipeline.

| Stage | Reality | Configurable now? |
|---|---|---|
| Chunking | `semantic-library/chunking.py`'s section-header-aware `chunk_resume_text()`. No overlap concept — not sliding-window | ✅ `max_chars` (200–5000), wired into `ResumeParser.py` |
| Embedding | OpenAI `text-embedding-3-small`, 1536-dim, hardcoded in `embeddings.py`. No second embedding path exists anywhere in this repo | ❌ **Not exposed** — editable would misrepresent what the code can do |
| Vector store | `LocalVectorStore` (JSON+numpy) is the only one actually instantiated. `AzureAISearchVectorStore` is real, complete code in `vector_store.py` but never selected/provisioned | n/a (one real knowledge source) |
| Retrieval | `rank_by_relevance()` — cosine similarity, was hardcoded `top_k=5`, no threshold | ✅ `top_k` (1–50) and **new** `similarity_threshold` (0.0–1.0), both wired into `ResumeParser.py` |
| Context assembly | Not classic RAG stuffing — map-then-reduce: each top-K chunk gets its own LLM summarization call, then a second call merges the summaries | n/a (architecture, not a tunable) |
| Prompts | `prompts/*.md` + `registry.json` were **documentation only** — `ResumeParser.py` hardcoded both templates inline, never read the `.md` files | ✅ **New**: versioned, runtime-loaded (`rag_prompt_versions`), seeded byte-for-byte from the existing `.md` files |
| Generation | `ChatOpenAI` hardcoded per call site (two different temperature/max_tokens pairs) | ✅ Both steps' model/temperature/max_tokens, wired into `ResumeParser.py` |
| Grounding/citations | None existed. No "insufficient evidence" fallback | ✅ **New**: `similarity_threshold` filtering + an explicit insufficient-evidence fallback response when every chunk is filtered out |
| Evaluation | No labeled query→relevant-doc dataset exists | ✅ Real retrieval-score/latency metrics from an actual preview run; Recall@K/Precision@K/MRR/NDCG explicitly **not shown** — no ground truth to compute them from |

## What's real, not a mockup

- `rag_config.py`'s `get_runtime_config()` / `get_active_prompt_template()`
  are called by `ResumeParser.py` itself (once per run, not per resume) —
  changing a setting in the console and activating it changes what the
  next resume-parsing run actually does. Fails soft to today's exact
  hardcoded defaults if Cosmos is unavailable or nothing's ever been
  activated — a config outage never breaks resume parsing.
- `rag_preview.py`'s retrieval preview runs against the **real on-disk
  index** (`semantic-library/index/resume-chunks.json`, 187 real chunks
  in this sandbox from actual pipeline runs) — not simulated. Generation
  preview makes genuine LLM calls (real cost), capped at 3 chunks.
- `get_source_status()` distinguishes **configured / connected / indexed
  / retrievable** as separate facts via a real probe query, never
  collapsed into one status.

## Lifecycle

Draft → Validate → Activate → Rollback, matching the Azure config
pattern:
- A draft never overwrites the active configuration until explicitly
  activated, and activation re-validates (defense in depth against a
  draft that was valid when saved but not anymore).
- Rollback restores a prior **activated** snapshot as a **new draft**
  (never a direct re-activation) — an admin must explicitly review and
  re-activate it, matching the spec's "revalidate restored configuration
  before activation."
- Prompt versions are never deleted; activating a new one marks the
  previous "superseded," not erased.

## RBAC (5 new permissions)

`rag.config.read`, `rag.config.write`, `rag.config.activate`,
`rag.preview.run`, `rag.prompts.manage` — only `admin` holds all five by
default.

## Tests

43 new tests in `tests/test_rag_config.py`: config validation (ranges,
unknown-field tolerance), runtime-config/prompt fallback behavior,
`rank_by_relevance()`'s new threshold parameter (including the
"everything filtered → empty, not an error" case), the real
`get_source_status()` probe, and every endpoint (draft save/reject,
activate incl. re-validation, history, rollback-creates-draft-not-direct-
activate, prompt draft/activate incl. missing-placeholder rejection,
preview incl. no-index/rate-limit/insufficient-evidence paths, RBAC).

**Found and fixed a real bug in the process**: the Azure Service Hub's
`_rate_limited()` helper always wrote to its own module-level
`_azure_check_last_run` dict regardless of which dict a caller intended —
the new RAG preview endpoint's rate limiter was silently sharing state
with Azure connection checks. Fixed by parameterizing `_rate_limited()`
with an explicit `store` dict; each feature now has its own counter.

Full suite: **451 passed** (up from 408).

## Gap-closure pass (same-day follow-up)

Four items flagged as deferred above were closed in a follow-up pass:

1. **Prompt-injection defenses** (spec section 8, explicitly required). Resume
   text and job descriptions are untrusted input a candidate fully controls.
   `rag_config.sanitize_for_prompt()` is now applied to every untrusted value
   (chunk text, JD text, and LLM-produced chunk summaries before the merge
   step) at every real call site (`ResumeParser.py`, `rag_preview.py`) before
   interpolation into a prompt. It does two concrete things: neutralizes
   triple-quote delimiter breakout (a resume containing a literal `"""`
   could otherwise prematurely close a template's quoted block), and flags
   common injection phrasing ("ignore previous instructions", etc.) inline
   with a visible marker rather than silently stripping it. This is defense
   in depth — not a claim that the pipeline is immune to a sufficiently
   creative adversarial resume.
2. **Explicit reindexing confirmation** (spec section 12). Activation now
   compares the draft's `chunking.max_chars` against the active
   configuration's; if it changed, the endpoint refuses with 409 and a
   message explaining that existing embeddings won't reflect the new
   boundaries until resumes are reprocessed, unless the request includes
   `acknowledge_reindex_impact: true`. The console's UI surfaces this as a
   second, specific confirmation dialog — never a silent retry.
3. **Draft-vs-active comparison** (spec section 11). The preview endpoint
   now accepts `compare: true`, running retrieval against both
   configurations' parameters for the same query and returning both side by
   side — generation is never run in compare mode, to avoid doubling real
   LLM cost on one click.
4. **Token usage in preview** (spec section 11). `run_generation_preview()`
   now wraps both LLM calls in LangChain's `get_openai_callback()` (the same
   mechanism `llm_utils.py` already uses for the real pipeline) and returns
   real `total_tokens`, not an estimate.

12 new tests added (`TestSanitizeForPrompt`, `TestChunkingChanged`,
`TestActivateReindexImpactGate`, `TestPreviewCompareMode`,
`TestGenerationPreviewTokenUsageAndSanitization`). Full suite: **463
passed** (up from 451).

## Known limitations / unsupported operations (stated, not hidden)

- **No hybrid/keyword search or reranking** is exposed — this repo's
  retrieval engine only implements cosine-similarity vector search.
  Listing unsupported strategies in the UI would be misleading.
- **Embedding model/provider is not configurable** — one hardcoded
  OpenAI model, no second path wired up anywhere.
- **No evaluation metrics requiring ground truth** (Recall@K,
  Precision@K, MRR, NDCG, citation correctness, groundedness, answer
  relevance) — no labeled query-relevance dataset or human/model-judge
  pipeline exists in this repo. Showing them would mean inventing
  numbers, which the spec explicitly forbids.
- **No automatic reindexing workflow**. Changing `chunking.max_chars`
  does not reprocess existing resumes or re-embed anything — the console
  says so explicitly; a real reindex would be a separate, explicit
  operation (not built this pass).
- **Generation preview is capped at 3 chunks** regardless of configured
  `top_k`, to bound real LLM cost/latency on every preview click.
- **Azure AI Search migration** is out of scope here — the code exists
  (`AzureAISearchVectorStore`) but switching the active backend is a
  separate, explicitly-validated operation per the spec, not something
  this config console does.
- Resource/connection validation for retrieval (`get_source_status()`)
  only covers the one real knowledge source this repo has; there is no
  multi-source selector since only one exists.
