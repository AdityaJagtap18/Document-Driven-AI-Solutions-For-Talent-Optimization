# AI Observability Center v2 — Instrumentation, Traces, Cost, Evaluation

This supersedes the first-pass `docs/AI_OBSERVABILITY_AZURE_HUB.md` for
the AI Observability Center specifically (Azure Service Hub is
unchanged). That earlier pass logged isolated per-call events; this pass
rebuilds the schema around real, hierarchical, multi-stage **traces**
(groups of correlated **spans**) and instruments one real workflow
**end to end**.

## Instrumentation map (what actually exists, verified before building)

| Spec's potential stage | Exists in this repo? | Where |
|---|---|---|
| Document uploaded / stored | Yes | `blob_store.py` (not traced this pass — see Known Limitations) |
| Document extraction (resume) | Yes, PyPDF2 (local, not Azure) | `ResumeParser.py:extract_text_from_pdf` — **traced** |
| Document extraction (verification) | Yes, Azure AI Document Intelligence | `document_intelligence_client.py` — traced (prior pass) |
| Chunking | Yes | `semantic-library/chunking.py` via `index_resume.py` — **traced** |
| Embedding generation | Yes, OpenAI | `semantic-library/embeddings.py` — **traced, now with real cost** |
| Vector indexing | Fused into embedding step (`LocalVectorStore.add()`), not a separate service | marked not-applicable, not invented |
| Retrieval / reranking | Yes — JD-relevance chunk selection | `index_resume.py:rank_by_relevance` — **traced** |
| Context construction / prompt assembly | Implicit in the LLM call's prompt string, not a separate traced step | not separately instrumented (see Known Limitations) |
| LLM request/response | Yes, 5 real call sites | `llm_utils.call_with_retry` → `observability.record_llm_call` — traced (prior pass, now trace-correlated) |
| Structured output parsing/validation | Yes | `ResumeParser.py`'s `json.loads()` block — **traced**, failure path distinguished from success |
| Grounding/citation checks | Does not exist — resume scoring doesn't cite retrieved chunks | not applicable, stated not invented |
| Safety checks | Does not exist — no moderation/safety pass anywhere in this repo | not applicable, stated not invented |
| Resume scoring | Exists in `ResumeMEtrics.py`, a **separate process** from `ResumeParser.py` | not traced this pass (see Known Limitations) |
| Interview-question generation / scoring | Exists, LLM-only | traced via `record_llm_call` (prior pass), not trace-correlated to a larger workflow (each is its own standalone single-span trace) |
| Persistence | Yes — Cosmos `upsert_candidate()` | `ResumeParser.py` — **traced** |
| Async/queue/trigger propagation | N/A — the one workflow traced end-to-end (`ResumeParser.py`) runs single-process, no queue hop | correctly out of scope |

**The one workflow instrumented fully end-to-end this pass: resume
parsing (`ResumeParser.py`)** — document extraction → chunking →
embedding generation → retrieval ranking → chunk-summarization LLM
calls → merge LLM call → output validation → persistence, all sharing
one `trace_id` per resume, with a real `correlation_id` (the
deterministic `candidate_id`). This was deliberately chosen as "the
highest-value end-to-end workflow" per the investigation: it's the only
place in this repo where multiple real AI stages execute in one process
call stack that can share one trace. `ResumeMEtrics.py` (scoring) runs
as a separate script/process, so it could not be joined into the same
trace without an architecture change out of scope for this pass.

## Trace/span schema

```
trace_id          groups every span of ONE end-to-end workflow run
span_id           unique per stage, never reused
parent_span_id    optional, for sub-stage hierarchy
correlation_id    external business id (e.g. candidate_id) to search by
workflow          trace-level concept -- same value on every span of one trace
                  (fixed a real bug this pass: record_llm_call() and
                  embed_texts() used to hardcode "llm"/"resume_matching"
                  regardless of what trace they were asked to join)
stage, status, duration_ms, metadata, error, logged_at
```

No separate `AITrace` document is persisted anywhere — a trace is
**computed** by grouping its spans by `trace_id` at read time
(`api_blueprint.py:_group_spans_into_traces()`). This avoids a second
write path that could drift from the span data; the spans are the only
source of truth, same principle the rest of this app's RBAC/page-access
features already use (policies/pages as the only source of truth, no
duplicate "summary" tables).

OpenTelemetry-COMPATIBLE (same trace/span/parent/correlation concepts),
not the real `opentelemetry-sdk` — this repo has no SDK dependency today
(only `azure-monitor-opentelemetry`, commented out, dormant). See
`observability.py`'s module docstring for the explicit reasoning.

## Persistence

Two sinks, same resilience pattern established in the prior pass:
1. **Always**: local JSON-lines (`logs/ai_traces.jsonl`,
   `logs/llm_observability.jsonl`) — zero configuration, guarantees
   telemetry never breaks the AI call it observes.
2. **Best-effort**: Cosmos DB's new `ai_trace_spans` container
   (partition `/trace_id`), same "fails soft, local write unaffected"
   dual-write pattern `ResumeParser.py` already used for its own
   `upsert_candidate()` call. **The API's read path currently reads the
   local JSONL file, not Cosmos**, even though spans are dual-written
   there — stated explicitly in Known Limitations below, not hidden.

## Metrics that are genuinely operational

Computed from real recorded data, verified via `tests/
test_ai_observability_azure_hub.py` and live in the browser:
- Total/succeeded/failed requests, failure rate (one row per trace, not
  per span).
- Average, P50, P95, P99 latency — **both** at span level (per stage)
  and trace level (end-to-end) — verified live showing e.g. "1586.3 ms
  average, 2110.9 ms P50" from 6 real demo traces.
- **Insufficient-sample handling is real**: `_percentile()` requires ≥5
  data points or returns `None`, and the UI shows an explicit "not
  enough data" message instead of a number (`LatencyRow` component) —
  unit-tested directly (`TestPercentile`).
- Cost by workflow and by model, with a documented, versioned pricing
  source (`model_pricing.py` — `PRICING_VERSION`, `PRICING_SOURCE`,
  `PRICING_LAST_UPDATED`, shown in the UI). Two real cost sources, each
  labeled: `langchain_openai_callback` (LLM chat calls — langchain's own
  real pricing table) and `documented_estimate` (embedding calls — a
  genuine prior gap, since `get_openai_callback()` doesn't wrap
  `embeddings.create()` the way it wraps chat completions; now computed
  from real token usage × this repo's own versioned price table).
- Instrumentation coverage: 8/8 documented stages observed in the demo
  data (100%), with explicit "not applicable to this repo" labels for
  stages that don't exist here (vector indexing as a separate service,
  safety checks, citation/grounding checks) — never silently omitted.
- Trace Explorer: real filtering (workflow, status, correlation_id
  substring, exact trace_id), real pagination (limit/offset, verified
  `count` is independent of page size), real sorting (`started_at` or
  `total_duration_ms`, asc/desc).
- Trace detail: full span-by-span timeline in execution order, each
  span's status/duration/model/tokens/cost/error — verified live on both
  a fully-succeeded trace and one with a failed `output_parsing_validation`
  span (the pipeline recovers via its existing filename-fallback, and
  the trace honestly shows that one stage failed even though the overall
  resume still got processed).

## RBAC integration

Two permission levels, not one, per the spec's access-control section:
- `observability.read` — aggregate summary + trace list (closer to a
  dashboard view).
- `observability.traces.read` — individual trace/span detail (closer to
  raw debugging information, a stronger permission). Verified
  (`test_adversarial_observability_read_alone_is_not_enough`) that
  holding only the weaker permission is correctly refused on the detail
  endpoint — these are two real, independently-checked gates, not one
  permission under two names.

Only `admin` (the single top-level role, see `docs/IAM_RBAC.md`) holds
either by default — verified live that a Recruiter account gets a real
403 on both the aggregate and the detail endpoint, from a direct API
call bypassing the UI entirely.

## Redaction and bounded payloads

- `observability._bound_metadata()` caps every metadata string value at
  300 characters (defense in depth — no real call site passes raw
  prompt/resume text into metadata today, but this bounds the payload
  regardless of what a future call site does). Verified
  (`TestBoundMetadata`).
- Span `error` text is NOT auto-redacted (document-worthy distinction):
  `azure_integrations.redact_secret()` is applied specifically to Azure
  connection-check errors (which can echo connection strings); AI pipeline
  errors (JSON decode failures, OpenAI errors) don't carry secret-shaped
  text in this repo's real failure modes, so adding the same redaction
  pass there would be security-theater, not a real protection — not
  applied.

## Tests

`tests/test_ai_observability_azure_hub.py` grew from 30 to **49 tests**
for this pass: `_percentile`/`_latency_stats` (including insufficient-
sample), `_group_spans_into_traces` (overall-status-is-worst-span-status,
correlation_id passthrough, exclusion of span-less-trace_id), `_cost_breakdown`
(only priced spans counted, pricing version surfaced), `_instrumentation_coverage`,
the rebuilt summary/list/detail endpoints (filtering, pagination, sorting,
404 on unknown trace, the two-permission-level RBAC split),
`model_pricing.estimate_embedding_cost` (known/unknown model, missing
token count), and the `workflow`-threading bug fix itself
(`test_golden_workflow_and_trace_id_are_threaded_through_not_hardcoded`).

**Full suite: `python -m pytest tests/` → 352 passed, 0 failed.**

Manually verified live in the browser (mock mode): Overview tab shows
real computed percentiles/cost/coverage from 6 demo traces; Trace
Explorer search-by-correlation-id correctly narrows to one trace; trace
detail correctly renders both a fully-succeeded 8-span trace and a
7-span trace with one failed stage (output validation) that still
recovers; a direct `getAiTrace()` call as Recruiter returned a real 403
citing the specific missing permission. Zero console errors throughout.

## Explicitly NOT built this pass (spec itself forbids faking these)

- **AI Evaluation Center** (datasets, versioned runs, comparison,
  fairness/consistency metrics across test groups) — no evaluation
  dataset or run infrastructure exists anywhere in this repo; building
  one from scratch (reference-labeled datasets, per-example outcome
  tracking, run comparison) is a substantial new subsystem, not an
  extension of existing code, and was out of scope for this pass.
- **Alert rules / alert events** — no real notification mechanism exists
  in this repo to wire alerts to (WhatsApp integration is candidate-
  facing, not an ops channel). The spec explicitly says *"do not show
  alerts as operationally active until their underlying mechanism
  works"* — so none were built, rather than shipping a UI that looks
  active but does nothing.
- **RAG quality metrics** (context precision/recall, faithfulness,
  answer relevance, citation correctness) — no evaluation harness or
  reference-labeled dataset exists to compute these from; the spec
  itself says not to claim these are available merely because a
  retrieval pipeline exists.
- **Cosmos-backed reads** — spans dual-write to Cosmos, but the list/
  detail/summary endpoints still read the local JSONL file only; a
  Cosmos-backed query path (for real server-side pagination/filtering at
  scale) is a real follow-up, not attempted here since this sandbox has
  no provisioned Cosmos to test it against.
- **`ResumeMEtrics.py` (scoring) and context construction/prompt
  assembly as distinct traced stages** — scoring runs as a separate
  process from parsing (can't share one trace without an architecture
  change); prompt assembly is implicit string formatting inside the
  already-traced LLM-call stage, not separately measured.
- **Full OpenTelemetry SDK adoption** — this pass is OTel-*compatible*
  (same concepts/field semantics) but doesn't add the `opentelemetry-sdk`
  dependency itself; Application Insights export remains dormant
  (package commented out in both `requirements.txt` files), unchanged
  from the prior pass.
