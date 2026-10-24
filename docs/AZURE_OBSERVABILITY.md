# Service Observability Report

A real, composed view across CDPI's actual subsystems — not an AI-only or
Azure-only page — surfaced at **Service Observability Report** (page_id
`azure_observability_report`, route `azure-observability-report`; the
page_id/route kept their original "azure" name from this feature's first
pass, documented here rather than silently renamed, since the running app's
URL/permission wiring already uses it). The one genuinely new observability
*capability* this feature adds is real Azure Monitor / Log Analytics
**query-back** (`azure_monitor_query.py`); every other section on the page —
per-service Azure connectivity, document/resume processing, external
integration delivery, local LLM/trace detail — reuses an existing real
endpoint rather than re-implementing it (see "Service inventory" below).

## Scope note: adapted from a generic spec

A later request for this feature described a configurable, drag-and-drop
widget workspace in **Angular + Spring Boot** terms. This repository's real
stack is **Python (Azure Functions) + a plain React-via-CDN frontend** (no
Angular, no Spring Boot, no bundler — see `frontend/README.md`), so the
Angular/Spring Boot references were not applicable and nothing was invented
to match them. The *intent* (cover all real CDPI services, not just AI/Azure,
and let the user choose what's shown) was implemented against the real stack,
with an explicit, agreed scope reduction: **simple show/hide widget toggles
persisted to `localStorage`**, not full drag-and-drop rearrange/resize or
server-persisted named layouts — this app has no existing per-user
dashboard-config persistence model, and building one plus a drag-and-drop
library integration was judged disproportionate to this app's actual scale
given the session's time budget. See "Known gaps" below.

## Service inventory (what this page actually covers, and its real source)

| Category | Real data source | New or reused |
|---|---|---|
| Azure Monitor-ingested requests/latency/AI usage/errors | `azure_monitor_query.py` → `manage/observability-report/*` | **New** |
| Per-service Azure connectivity/health (Cosmos DB, Key Vault, Azure OpenAI, Blob Storage, etc.) | `manage/azure-readiness` (existing) | Reused |
| Document/resume processing (extraction attempts, success/failure) | `manage/reports/resume-processing` (existing, real `document_extraction` spans) | Reused |
| External integrations (WhatsApp delivery; email has no delivery logging anywhere in this repo) | `manage/reports/communication-delivery` (existing) | Reused |
| Local LLM call tokens/cost/latency, full trace/span detail | AI Observability Center (existing page, linked out, not duplicated) | Reused |
| Real Azure resource cost | Azure Readiness' Cost & Usage tab (existing, linked out) | Reused |

Categories from the generic spec with **no real component in this repo** —
not built, not faked: a separate frontend-JS-error telemetry pipeline,
a relational/SQL database (this app uses Cosmos DB only), a background job
queue (the blob-triggered pipeline has no queue/backlog concept — see
`manage/reports/resume-processing`'s own `pending_queue_depth: null`), and
offer generation/approval/acceptance (confirmed absent in this session's
earlier end-to-end pipeline audit, `docs/E2E_CANDIDATE_LIFECYCLE_REPORT.md`).

## Widgets / charts

| Widget | Type | Real data source |
|---|---|---|
| Executive KPIs | KPI cards | `manage/observability-report/summary` (Azure Monitor + local) |
| Request Volume Over Time | Line chart, succeeded vs. failed | Azure Monitor (`get_request_volume`) |
| AI Model Usage | Table | Azure Monitor (`get_ai_usage`) |
| AI Token Usage by Model | Bar chart | Azure Monitor (`get_ai_usage`) — same data as the table above, charted |
| Error Analysis | Table, most recent first | Azure Monitor (`get_recent_errors`) |
| Azure Service Health Matrix | Table | `manage/azure-readiness` (existing, reused) |
| Service Health Breakdown | Donut, count by status | `manage/azure-readiness` — a service with `UNKNOWN` status is its own slice, never folded into "healthy" |
| Traces by Workflow: Succeeded vs Failed | Stacked bar, per real instrumented workflow (`resume_parsing`, `resume_matching`, `llm`, `azure_config`, `azure_ephemeral_test`, `azure_ai_foundry`, `observability_self_test`) | Local `ai_traces.jsonl`, via `summary.local_observability.ai_traces.by_workflow` |
| Average Duration by Workflow | Bar chart | Same `by_workflow` data, a different cut — where time is actually going |
| Document / Resume Processing | Stat tiles + failure table | `manage/reports/resume-processing` (existing, reused) |
| External Integrations | Table by channel | `manage/reports/communication-delivery` (existing, reused) |
| Local Observability Recap | Text summary, links to AI Observability Center | `summary.local_observability` |

All charts use **Chart.js** (already loaded via CDN in `frontend/index.html` for `AdminReports.jsx`) through the existing `ChartCanvas`/`ChartCard`/`CHART_COLORS`/`PALETTE` helpers — no second charting library was added.

## Dashboard customization

A **Customize** toggle on the page opens a panel listing every section
(`OBS_REPORT_WIDGETS` in `AzureObservabilityReport.jsx`) with a checkbox.
Hiding a section is a display preference only — its data still loads, so
toggling it back on is instant, not a re-fetch. State persists to
`window.localStorage` under `cdpi_observability_report_hidden_widgets`,
**per browser/device, not per account** — there is no backend
dashboard-config model in this repo to persist it server-side. A "Restore
default (show all)" action clears the preference.

## What already existed vs. what this feature adds

| Capability | Where | Status before this feature |
|---|---|---|
| Local LLM call token/cost/latency, per-trace spans, Trace Explorer | AI Observability Center (`frontend/src/components/AIObservabilityCenter.jsx`, `manage/ai-observability/*`) | Already real, already working — reused here, not re-implemented |
| Per-service Azure connectivity/health, real resource list, real month-to-date cost | Azure Readiness (`AzureReadiness.jsx`, `manage/azure-readiness/*`) | Already real, already working — reused here, not re-implemented |
| Telemetry **write path** (local JSONL always, Cosmos best-effort, Application Insights push via OpenTelemetry logging handler) | `observability.py` | Already real, already working |
| Telemetry **query-back** (reading already-ingested data OUT of Azure Monitor/Log Analytics) | `azure_monitor_query.py`, `manage/observability-report/*` | **Did not exist anywhere in this repo before this feature.** All prior "Azure Monitor integration" was write-only/push-only. |

## Required configuration

| Variable | Source | Required for |
|---|---|---|
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Application Insights resource's connection string | The write path (`observability.py`) — must already be ingesting data for there to be anything to query back |
| `AZURE_LOG_ANALYTICS_WORKSPACE_ID` | The Log Analytics workspace's **customerId** GUID (not its resource name) behind that Application Insights resource | The read path (`azure_monitor_query.py`) |

Find the workspace ID with:
```bash
az monitor log-analytics workspace show --resource-group <rg> --workspace-name <name> --query customerId
```

## Authentication

`azure_monitor_query.py` uses `AzureCliCredential` — the same `az login`
session every other Azure CLI-dependent feature in this repo already relies
on (see `az_cli.py`). No separate service principal or managed identity is
configured. Running this locally requires a real, unexpired `az login`
session with **Log Analytics Reader** (or broader) access on the workspace.

## Azure permissions required

The signed-in `az` identity needs read access to the Log Analytics workspace
— in this environment, that came from the Microsoft Learn sandbox account's
Owner role scoped to the resource group containing the workspace. In a
production subscription, the minimum real RBAC role is **Log Analytics
Reader** on the workspace (or the containing resource group/subscription).

## Telemetry sources and the real schema (confirmed, not assumed)

This environment's Application Insights resource (`appi-cdpi-audit1`) is
**workspace-based**, which means ingested structured log records land in the
**`AppTraces`** table (not the classic `traces` table some documentation
examples use), with:
- `TimeGenerated` — ingestion timestamp (not the original log timestamp)
- `Message` — literally `"ai_trace"` or `"llm_call"` (the first positional
  arg to `logger.info(...)` in `observability.py`)
- `Properties` — a JSON **string** column

### A real, confirmed limitation: `custom_dimensions` is Python-repr text, not JSON

`observability.py` logs via `logger.info(message, extra={"custom_dimensions":
record})`, where `record` is a **nested** dict (it has a `metadata`
sub-dict for `ai_trace` rows). OpenTelemetry's logging-attribute model only
supports flat, primitive attribute values — it cannot represent a nested
dict as structured columns. The Azure Monitor exporter falls back to
Python's `str()`/`repr()` of the whole `record` dict as **one string value**
under the `custom_dimensions` key inside `Properties`. That string uses
Python-repr syntax (single-quoted strings, `None`/`True`/`False`), **not
valid JSON** — `parse_json()` cannot parse it.

`azure_monitor_query.py` works around this today by extracting individual
fields out of that string with KQL `extract()` regexes (see `_extract()`
and `_CD_EXPR`). This was verified against real ingested data while building
this feature — confirmed real field names, real nesting, real values,
not assumed from documentation.

**This is flagged as a known gap, not silently worked around forever**: the
durable fix is changing `observability.py` to log flat, JSON-safe top-level
attributes (or `json.dumps()` the nested `metadata` sub-dict specifically)
so future telemetry can be queried with `parse_json()` directly instead of
regex extraction. Not done in this pass — out of scope (see "Known gaps"
below).

## Metric definitions

| Metric | Definition | Source |
|---|---|---|
| Total requests | Count of real `ai_trace`-type log records ingested into `AppTraces` in the window | Azure Monitor |
| Success / failure rate | `succeeded` / `failed` status field on those same records, divided by total | Azure Monitor |
| p50 / p95 / p99 latency | Percentile of each record's real `duration_ms` field — **not computed if fewer than 20 samples exist in the window** (shown as `null` with an explicit "too few samples" detail, never silently computed from noise) | Azure Monitor |
| AI inference requests / tokens / cost | Count / sum of real `llm_call`-type records' `total_tokens`/`cost_usd` fields, grouped by `model`/`provider` | Azure Monitor |
| Telemetry ingestion confirmed | `true` only if a real `ai_trace` or `llm_call` row was found in the last 24h — **a configured connection string alone is never sufficient** | Azure Monitor |
| `overall_status` | `NOT_CONFIGURED` (no workspace ID set) → `UNKNOWN` (workspace unreachable) → `CONFIGURED_NO_INGESTION` (reachable, zero rows found) → `DEGRADED` (failure rate > 10%) → `HEALTHY`. Computed server-side from the above real evidence — never set to `HEALTHY` just because this endpoint itself returned 200 | `manage/observability-report/summary` |

All local-sourced numbers (`local_observability` in the summary response) are
explicitly labeled as coming from this process's own JSONL logs, and are
**never merged into the same number** as the Azure-sourced `azure_telemetry`
section — the two can legitimately disagree (export lag, export failures,
or simply because local logs include this run's in-flight data that hasn't
reached Azure yet).

## Query behavior, caching, and timeouts

- Every query has a real 20-second server-side timeout
  (`_QUERY_TIMEOUT_SECONDS` in `azure_monitor_query.py`).
- `api_blueprint.py`'s observability-report endpoints cache each distinct
  `(query, hours)` result for 60 seconds in-process (`_cached_query`), so a
  dashboard page with several charts doesn't fire a fresh Log Analytics
  query per chart per render. This is a simple in-memory cache (per
  Functions-host process) — fine for this scale, not a distributed cache.
- `hours` is validated server-side (must be `> 0` and `<= 168`); an invalid
  value returns `400`, never a silently-clamped or fabricated range.

## Cost calculation limitations

AI cost shown on this page (`ai_total_cost_usd`) sums the real `cost_usd`
field already computed and recorded at call time by `model_pricing.py` (see
that module and `observability.py`'s `record_llm_call()`) — it is **not**
re-derived from Azure billing data. `model_pricing.py`'s own
`PRICING_VERSION`/`PRICING_SOURCE`/`PRICING_LAST_UPDATED` fields (visible on
the AI Observability Center page) are the authoritative source/effective
date for that pricing; this page doesn't duplicate them.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `overall_status: NOT_CONFIGURED` | `AZURE_LOG_ANALYTICS_WORKSPACE_ID` isn't set |
| `telemetry_health.reason: authentication_failed` | No active `az login` session, or it expired (Microsoft Learn sandbox Temporary Access Passes in particular expire fast — see this repo's other Azure docs) |
| `telemetry_health.reason: authorization_denied` | The signed-in identity lacks Log Analytics Reader (or broader) on the workspace |
| `ingestion_confirmed: false` but `azure_configured: true` | The workspace is reachable, but no `ai_trace`/`llm_call` row was found in the last 24h — either nothing has run recently, or Application Insights export (`observability.py`) is failing/disabled |
| A KQL `SemanticError` / `SEM01xx` in `detail` | A real Log Analytics query failure — check the table/column names in `azure_monitor_query.py` still match the workspace's actual schema (workspace-based vs. classic Application Insights have different table names: `AppTraces` vs. `traces`) |

## Test results

`pytest tests/test_azure_monitor_query.py` — 19/19 passed, all mocked (no
real network calls; clearly labeled `golden`/`edge`/`adversarial` per this
repo's test-naming convention). See that file for exact coverage: query
success, query failure categorization (auth/authorization/not-found/
endpoint-unreachable/unknown), dependency-missing fail-closed behavior,
insufficient-sample-size percentile suppression, and result shaping for
every public function.

Full repo test suite after this feature (including the RBAC-permission
assertion test updated for `azure_cloud_administrator` gaining
`observability.read`): **670 passed, 1 failed** in 133.72s. The one failure
(`tests/e2e/test_azure_readiness_smoke.py`) is a pre-existing, unrelated
Selenium smoke test for the mock-mode Azure Readiness page — it exercises
none of this feature's code, spins up its own Chrome/local-server instance,
and was already failing intermittently before this feature in the same
session (see `docs/E2E_CANDIDATE_LIFECYCLE_REPORT.md`'s Section G, item 8) —
consistent with an environmental flake (chromedriver/timing under a busy
dev session), not a regression this feature introduced.

Full backend real-integration verification (against the actual provisioned
Log Analytics workspace, `az login`'d for real) was performed manually while
building this feature — see `docs/AZURE_REAL_SERVICES_AUDIT.md`'s entry for
this feature for the exact real query results captured. Verified live in the
browser, logged in as `auditadmin`: the Service Observability Report page
renders with real KPIs (78 real Azure-ingested requests, 100% success rate,
p95 latency 9785.7ms, 13 real AI calls / 9004 tokens / $0.042 cost), a real
request-volume line chart, a real AI-token-usage bar chart, a real
service-health donut (NOT_CONFIGURED/PARTIALLY_VERIFIED/READY/NOT_INTEGRATED
slices), real per-workflow succeeded/failed and average-duration bar charts
across all 7 real instrumented workflows, a real Azure Service Health
Matrix table, real document-processing stats (2 attempts, 2 succeeded),
and real WhatsApp delivery stats (7 submitted, 4 provider-accepted, 3
failed) — plus a working Customize panel (toggle, `localStorage`
persistence confirmed via direct inspection, restore-default).

## Known gaps

1. **`custom_dimensions` is logged as Python-repr text, not JSON** (see
   above) — regex extraction works today but is fragile. The durable fix
   (flat/JSON-safe logging attributes in `observability.py`) is not done
   in this pass.
2. **No automated integration test runs a real query against live Azure**
   — only the manual verification described above and in the audit doc.
   An automated real-integration test would need a dedicated, stable test
   workspace and credentials wired into CI, which doesn't exist here.
3. **Resource Utilization (CPU/memory/throttling)** is not implemented —
   no such metrics are collected anywhere in this stack. The page does not
   fabricate values for this; it simply doesn't have a section for it.
4. **Distributed trace-to-span drill-down** is not duplicated on this page
   — use the AI Observability Center's Trace Explorer (already real,
   already complete) via the correlation IDs shown in this page's
   Error Analysis table.
5. **CSV export** covers the request-volume time series only (not every
   chart on the page) — scoped this way deliberately to keep the export
   surface small and auditable, given time budget.
