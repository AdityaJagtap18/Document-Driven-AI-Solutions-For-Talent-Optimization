# AI Observability Center and Azure Service Hub

Two new Admin Panel modules, built on the existing RBAC/Page Access
Management system (`docs/IAM_RBAC.md`, `docs/PAGE_ACCESS_MANAGEMENT.md`)
rather than a parallel app or mechanism.

## 1. Repository findings (Phase 1)

Verified by direct inspection, not assumed from docs/config:

- **Observability already had a real foundation.** `observability.py`
  writes one JSON line per LLM call to `logs/llm_observability.jsonl`
  (`record_llm_call()`), and `llm_utils.py`'s `call_with_retry()` is
  wired into it at 5 real LLM call sites (`ResumeParser.py` x2,
  `generate_questions.py`, `interview_bot.py`, `voicebot/voice_bot.py`).
  `GET /observability/summary` already aggregated this for
  `AdminReports.jsx`/`Dashboard.jsx`. Application Insights emission code
  exists in `observability.py` but is dormant — `azure-monitor-opentelemetry`
  is commented out in both `requirements.txt` files.
- **Two real AI pipeline stages had zero observability**: embedding
  generation (`semantic-library/embeddings.py`) and document extraction
  (`document_intelligence_client.py`) — neither logged anything before
  this feature.
- **Azure integrations actually referenced in code** (verified real SDK
  usage, not just env-var placeholders): Cosmos DB and Blob Storage
  (`context-lake/client.py`, `context-lake/blob_store.py`), Azure AI
  Document Intelligence (`document_intelligence_client.py`, explicitly
  untested against a live resource per its own docstring), Azure AI
  Speech (`voicebot/azure_speech_utils.py`, same untested caveat), Key
  Vault (`secrets_loader.py`). `whatsapp_client.py` is Meta's Graph API,
  not Azure — correctly excluded.
- **No health-check, resource-inventory, or cost code existed anywhere**
  (`azure-mgmt-*`, `ResourceManagementClient`, `CostManagementClient`:
  zero hits in the whole repo).
- **No runtime-configurable theme system exists** — confirmed absent
  (zero CSS, zero component, zero permission; `auth.py`'s own docstring
  already noted this when the IAM feature was built). Not invented here
  either, consistent with that precedent.
- **No rate limiting existed anywhere** in `api_blueprint.py` or
  elsewhere.
- **Admin UI convention**: page-id-gated tabs in `App.jsx` (no router),
  `require_permission()` on every endpoint, `table-card`/`candidate-table`/
  `badge`/`modal-backdrop` CSS classes, `stat-tile`/`stat-row` for KPI
  tiles. Reused throughout rather than inventing new patterns.

## 2. Architecture

- **`observability.py`** (extended, not replaced): `record_llm_call()`
  unchanged in behavior, but now ALSO calls the new `record_ai_trace()`
  internally (one instrumentation point → two views, not two separate
  call-site changes — this feature's own "avoid duplicate
  implementations" requirement). New `record_ai_trace()` /
  `trace_stage()` (context manager) write to `logs/ai_traces.jsonl`, one
  entry per pipeline stage, with `trace_id`/`correlation_id`/`workflow`/
  `stage`/`status`/`duration_ms`/`metadata`/`error`. Same dormant
  Application Insights emission path as LLM calls (OpenTelemetry-
  compatible structured log records), same local-file-always-works
  guarantee.
- **Wired into real call sites**: `semantic-library/embeddings.py`'s
  `embed_texts()` (workflow `resume_matching`, stage
  `embedding_generation`) and `document_intelligence_client.py`'s
  `analyze_document()` (workflow `document_verification`, stage
  `document_extraction`) — both previously-unobserved stages. Deeper
  instrumentation of `ResumeParser.py`'s overall orchestration was
  deliberately left out of scope (background script, thin pytest
  coverage, higher risk for the value) — stated here, not hidden.
- **`azure_integrations.py`** (new): a static, code-verified registry of
  the 5 real Azure integrations plus Application Insights, each with
  `required_secrets`, `source_module`, and `live_check_supported`.
  `check_connection()` returns one of `not_configured` / `connected` /
  `error` / `configured_unverified` — **never a fabricated "connected"**.
  Live checks only exist for Cosmos DB (`database.read()`) and Blob
  Storage (`get_account_information()`) — both free, idempotent,
  side-effect-free SDK calls. Document Intelligence/Speech/Key
  Vault/Application Insights report `configured_unverified` because
  their only cheap operations are real, billable ones this feature won't
  spend Azure quota on just to paint a status dot.
- **`redact_secret()`**: strips `key=`/`password=`/`token=`-style
  patterns, `Bearer` tokens, and long base64-looking blobs from any
  error text before it can reach an API response or the audit log, and
  caps length — applied to every `check_connection()` error.
- **New permissions**: `observability.read`, `azure.read`, `azure.manage`
  (modules "AI Observability", "Azure Service Hub"), added to the
  existing `auth.PERMISSIONS` catalog. Only `admin` (the single top-level
  role, see `docs/IAM_RBAC.md`) holds them by default.
- **New pages**: `ai_observability` (route `ai-observability`) and
  `azure_services` (route `azure-services`), added to `auth.PAGES` — the
  same code-defined route registry the Page Access Management feature
  established (this app has no client-side router). Access-policy-seeded
  for `admin` automatically via the existing `sync_pages()`/
  `default_page_access_policies()` mechanism, no new seeding logic
  needed.
- **Frontend**: `AIObservabilityCenter.jsx` and `AzureServiceHub.jsx`,
  gated in `App.jsx`'s nav exactly like every other admin page (via
  `GET /auth/me`'s `pages` array). Reuses existing global components
  (`StatTile` from `Dashboard.jsx`) rather than redefining them — this
  app has no bundler/module system, so every component file shares one
  global scope, and redefining a same-named function would have silently
  shadowed the original.

## 3. Admin routes and APIs added

- `GET /admin/ai-observability/summary` (`observability.read`) — merged
  LLM-call metrics (reused from the existing endpoint's logic via a new
  shared `_llm_observability_summary()` helper, not duplicated) +
  AI-trace summary by workflow + last 50 traces.
- `GET /admin/ai-observability/traces` (`observability.read`) — filtered
  (`?workflow=`, `?status=`), capped (`?limit=`, max 1000) raw trace
  list.
- `GET /admin/azure-services` (`azure.read`) — the integration registry
  plus **secret presence only** (never values), no network calls (fast).
- `POST /admin/azure-services/{integration_id}/check` (`azure.manage`) —
  runs a real check per `azure_integrations.check_connection()`,
  rate-limited (10s per actor+integration, in-memory — a documented
  per-process limitation under multi-instance scale-out, not solved with
  a distributed store this feature doesn't need yet), audit-logged as
  `azure_connection_checked`.

All four require the same `require_permission()` gate as every other
endpoint — no separate/weaker authorization path, and `GET /auth/me`'s
existing `pages` array is the only thing that changes frontend nav
visibility, verified live (see §7).

## 4. Data integrity (Phase 4)

- Every number in the AI Observability Center is read live from
  `logs/llm_observability.jsonl`/`logs/ai_traces.jsonl` — an empty/missing
  file returns real zeros, not placeholder data (tested:
  `test_golden_empty_logs_return_real_zeros_not_fake_data`).
- Azure Service Hub never shows "connected" unless a live check actually
  succeeded. In this sandboxed dev environment (no real Azure resources
  provisioned), every integration genuinely reports `not_configured` —
  verified live in the browser, not asserted.
- **Mock mode demo data** (`mockApiClient.js`'s `MOCK_AI_TRACES`,
  `MOCK_AZURE_INTEGRATIONS`) is isolated behind this app's existing,
  already-established dev-only indicator (`window.CDPI_CONFIG.mode`, the
  topbar's "API: mock" label) — and the Azure Service Hub page adds its
  own explicit banner on top: *"Mock mode: no real secrets exist in this
  browser-only demo... Switch config.js to mode:'real'... to see actual
  status."* Mock-mode `checkAzureIntegration()` always returns
  `not_configured` too — it does not fabricate a "connected" result even
  as illustrative demo data.
- No resource inventory or cost data is shown anywhere — this repo has
  no `azure-mgmt`/Cost Management integration, and building one wasn't
  attempted (no real Azure subscription credentials in this environment
  to test against). Documented as a known limitation (§8), not silently
  dropped.

## 5. Security (Phase 5)

- Server-side authorization on every endpoint (`require_permission()`),
  least-privilege (`azure.read` view vs. stronger `azure.manage` to
  trigger a real outbound call).
- Secret values are never returned by any endpoint — `GET
  /admin/azure-services` returns only a `{secret_name: bool}` presence
  map (verified: `test_golden_reports_secret_presence_never_values`
  asserts the literal secret string never appears in the response body).
- `redact_secret()` scrubs connection-string/key/token patterns from any
  live-check error before it reaches a client or the audit log
  (verified: `test_adversarial_live_check_failure_is_reported_and_redacted`).
- Rate limiting on the one endpoint that makes a real outbound network
  call (`POST .../check`), 429 on a repeat within 10s.
- Managed-identity-style credential handling already existed
  (`secrets_loader.py`'s `DefaultAzureCredential`) — reused, not
  reinvented.
- Audit logging: every connection check is recorded
  (`azure_connection_checked`, actor/integration_id/status/timestamp via
  the existing `_audit_admin_action()` helper — reused, not duplicated).

## 6. Persistence and synchronization

No new Cosmos containers. `logs/llm_observability.jsonl` and
`logs/ai_traces.jsonl` are the only persistence for observability data —
same zero-configuration-required design the existing LLM metrics already
used, extended rather than replaced. The Azure integration registry is a
static code-defined catalog (`azure_integrations.AZURE_INTEGRATIONS`),
not persisted data — there's nothing to synchronize, it's reconciled
against real secret presence on every read.

## 7. Tests executed and results

`tests/test_ai_observability_azure_hub.py` — **30 new tests**, all
passing: `record_ai_trace()`/`trace_stage()` (success/failure paths,
unique trace ids), `record_llm_call()` now also emitting a trace,
`azure_integrations.check_connection()` (not-configured/
configured-unverified/connected/error/unknown-integration paths),
`redact_secret()`, and the four new endpoints (authorized/unauthorized
access, empty-data honesty, filtering, rate limiting, secret-presence-
never-values, audit logging).

**Full suite: `python -m pytest tests/` → 333 passed, 0 failed** (303
pre-existing + 30 new, all green — no environment-related failures to
report separately).

Manually verified live in the browser (mock mode): Admin sees both new
nav tabs; AI Observability Center renders real stat tiles, per-workflow
breakdown, per-label LLM table, and a filterable trace list (filtering
to `document_verification` correctly narrowed the list); Azure Service
Hub renders all 6 integrations with the mock-mode banner, clicking
"Check connection" on Cosmos DB returned an honest `not_configured`
result naming the exact missing secrets; Recruiter's nav has neither new
tab; a direct `listAzureIntegrations()` call as Recruiter returned a
real 403. Zero console errors throughout.

## 8. Known limitations (stated, not hidden)

- No Azure resource inventory or cost data — no `azure-mgmt`/Cost
  Management SDK integration exists or was added; this sandboxed
  environment has no real Azure subscription to build/test one against.
- Document Intelligence, Azure Speech, Key Vault, and Application
  Insights get configuration-presence checks only, never a live
  connectivity check — their only cheap operations are billable, and
  this feature deliberately won't spend Azure quota just to populate a
  status page.
- Rate limiting is in-process/in-memory — under a multi-instance scaled-
  out Azure Functions deployment, each instance has its own counter, so
  the effective limit is `N × 10s` across `N` instances, not a true
  distributed 10s window.
- Application Insights emission (shared by LLM-call and AI-trace
  records) remains dormant in this environment — the dependency is
  commented out in both `requirements.txt` files, consistent with how
  this repo has always treated it; nothing new was fabricated to make it
  look active.
- Deeper AI-pipeline tracing (e.g. `ResumeParser.py`'s full ingestion
  orchestration, candidate scoring in `ResumeMEtrics.py`) was not
  instrumented — scoped to the two previously-completely-unobserved real
  Azure/embedding call sites for this pass, not the entire pipeline.
