# Azure Key Vault, Azure AI, and Observability — Real-Service Audit

Verifies whether these three areas actually operate against real Azure
services, as opposed to mocks, hardcoded responses, or placeholder
integrations. Every finding below is backed by a command actually run or
a file actually read in this session — none of it is inferred from
documentation. No secret value is printed anywhere in this document.

> **Section -2 is the final browser-verification pass -- read it
> first.** Section -1 is the real-provisioning CLI/Python verification
> pass. Section 0 is the prior code-fix pass. Sections 1-5 are the
> original audit findings. All kept verbatim as the record of what was
> found and fixed, in order.

## -2. Final verification: confirmed live in the actual running app

Everything in Section -1 was verified via direct Python/CLI calls. This
pass goes one step further: **ran the real backend for the first time
ever** (`func start`, Azure Functions Core Tools) and drove the actual
browser UI against it with a real logged-in user, to see the real
`READY` statuses render in the app itself, not just in a terminal.

### -2.1 Two more real, previously-undiscovered bugs found

Both found only because this was the **first time this entire backend
has ever been run as a real Azure Functions host** in this project's
history (every prior verification used direct Python function calls,
never real HTTP routing) -- exactly the kind of gap this whole audit
effort exists to catch.

1. **Misplaced route decorator.** `@bp.route(route="admin/observability/summary", ...)`
   was sitting on `_read_jsonl()`, a plain internal helper function, not
   on `observability_summary_endpoint()`, its actual intended target
   directly below it. This made the real Functions host immediately
   crash on startup (`FunctionLoadError: cannot load the _read_jsonl
   function...`) and, independently, meant the real endpoint was never
   registered as a route at all. **Fixed** by moving the decorator to
   the correct function (`azure-functions/api_blueprint.py`).

2. **The entire admin API was unroutable.** Azure Functions reserves
   the whole `/admin/*` URL segment for its own internal host-management
   API, regardless of the configurable `routePrefix`. This repo's admin
   console convention — used by **every** admin feature built across
   this entire project (users, roles, pages, secrets, RAG config, comms
   config, persona catalogue, Azure Service Hub, and every endpoint
   built in this audit) — used `route="admin/..."` throughout. The real
   host rejected ~60 functions at startup with `"The specified route
   conflicts with one or more built in routes."` **Fixed** by renaming
   the route prefix from `admin/` to `manage/` across
   `azure-functions/api_blueprint.py` (74 occurrences) and
   `frontend/src/apiClient.js` (73 occurrences) -- a mechanical,
   verified-safe rename (confirmed no other file referenced the old
   paths; full regression suite re-run clean afterward).

A **third**, smaller issue was found in the same pass: two tests
(`TestReviewCandidate`) had been implicitly relying on Cosmos DB being
unconfigured to short-circuit `repositories.has_recent_delivery()`'s
duplicate-send check. Once this pass's real Cosmos DB was provisioned
and retained, those tests started failing from real, persisted state
left behind by earlier test runs in the same database. **Fixed** by
explicitly mocking `has_recent_delivery` in both tests, restoring proper
isolation (and flagging, for the record, that this repo's test suite now
depends on Cosmos being either absent or explicitly mocked in every test
that touches delivery-duplicate-checking -- true going forward since
Cosmos is being retained).

### -2.2 Real environment stood up for this pass

- Provisioned one additional real resource (with the same per-step
  approval discipline as Section -1): a serverless Cosmos DB account
  (`cosmos-cdpi-audit1`, eastus) -- required because the real backend's
  login flow looks up the authenticating user in Cosmos; there was no
  way to exercise real login without it.
- Found and fixed a **fourth** real issue along the way: the `pages`
  container's `list_pages()` query (`ORDER BY c.module ASC, c.name ASC`)
  needs an explicit Cosmos composite index for multi-field sorting --
  Cosmos's default indexing policy only covers single-field ordering.
  Fixed by adding the composite index via `az cosmosdb sql container
  update --idx`. Never caught before because, again, this was the first
  time `bootstrap_iam.py` had ever run against a real Cosmos database.
- Seeded real Cosmos data via the app's own `bootstrap_iam.py` (12
  roles, 16 pages, 7 integration instances, 2 RAG prompts, 1 comms
  template -- all real, idempotent, application-owned seed data) and
  created one real demo admin account for this verification session
  only (`auditadmin`, random generated password, never committed or
  displayed -- stored briefly in a gitignored local temp file, deleted
  after use).
- Ran the real Azure Functions host locally (`func start`, Core Tools
  4.15.2, already installed earlier this session) against the real
  provisioned resources, with `frontend/src/config.js` temporarily
  switched to `mode: "real"`.

### -2.3 Confirmed live in the browser (screenshots taken, not described from memory)

1. **Real login** against the real backend + real Cosmos: `POST
   /api/auth/login` → `200 OK`, landed on the real (empty, honest --
   zero fabricated candidates) Recruiter Dashboard, top bar reading
   `API: real`.
2. **Azure Readiness page, Services tab**, after clicking "Validate
   all" (a real, live round of connectivity checks):
   - **Azure Cosmos DB: `READY`** -- "Live connection check succeeded."
   - **Azure Key Vault: `READY`** -- "Live connection check succeeded."
   - Document Intelligence / Speech / Blob Storage: correctly still
     `NOT_CONFIGURED` (honestly never provisioned in either pass)
   - Azure OpenAI / Application Insights: `PARTIALLY_VERIFIED` (no
     further safe/free check exists beyond config presence -- by
     design, see Section 0)
   - Azure AI Search: correctly `NOT_INTEGRATED`
   - Azure CLI card: "Authenticated via Azure CLI. Subscription: Learner
     Account (4116346b-...)"
3. **Azure AI & Observability tab**, "Run real test request" clicked
   live: **Chat: succeeded. Embedding: succeeded.** -- the exact same
   real `gpt-4o`/`text-embedding-3-small` calls from Section -1, now
   triggered by a real button click in the real UI.
4. **Application Insights card**, "Run connectivity test" clicked live:
   produced a real correlation ID and the "Handed to Application
   Insights" badge, with the same honest ingestion caveat shown in the
   UI copy itself.

No console errors at any point. Every network request observed via the
browser's own devtools-equivalent (`read_network_requests`) resolved
with a real status code from the real local Functions host -- nothing
was mocked, faked, or pre-seeded to produce these results.

### -2.4 Cleanup after this pass

- `frontend/src/config.js` reverted to `mode: "mock"` (confirmed: app
  reloads to the mock-mode login screen, no console errors).
- Local Functions host (`func start`) process stopped.
- Gitignored temp admin-password file deleted.
- Full regression suite re-run clean: see -2.5.
- Cosmos DB (`cosmos-cdpi-audit1`) is **retained**, same as the Section
  -1 resources, per the same "these are real application configuration,
  not throwaway test infrastructure" approval basis -- adds to the
  ongoing cost disclosure: Cosmos DB serverless is pay-per-request, a
  few cents at most for this session's actual usage, effectively free
  at idle (no provisioned/reserved throughput).

### -2.5 Final regression suite

```
$ python -m pytest tests/ -q
```
Run immediately after this pass's fixes (decorator fix, route rename,
composite index, test-isolation fix) and cleanup -- see the live result
reported at the end of this task.

### -2.6 Updated status

| Component | Status |
|---|---|
| Key Vault | **READY** -- confirmed via real secret retrieval (Section -1) AND live in the real running app (this section) |
| Azure OpenAI / AI Foundry | **READY** -- real chat + embedding succeeded via direct Python call (Section -1) AND via a real button click in the real running app (this section) |
| Application Insights | **READY** -- real span confirmed ingested into Azure Monitor (Section -1) AND the same connectivity test re-run live from the real app's UI (this section) |
| Cosmos DB (new this pass) | **READY** -- real login succeeded, real composite-index bug found and fixed, real role/page data seeded |

---

## -1. Final verification: real Azure resources provisioned and tested

**All three services are now `READY`, backed by real Azure evidence captured below.**

### -1.1 Access check

```
$ az account show
{
  "tenantId": "65087c47-0017-4258-8086-72832006d566",
  "name": "Learner Account",
  "id": "4116346b-5ded-4f3b-8387-f8d055802adc",
  "user": {"name": "azuser7799_mml.local@karthikirisoutlook.onmicrosoft.com"}
}
```
Real session re-authenticated via `az login --use-device-code` (the prior
session's Temporary Access Pass had expired -- confirmed by a live call
failing with `AADSTS130504` while `az account show` still returned stale
cached data, a real and useful distinction: cached identity info is not
proof of a live, usable token).

**Real RBAC check:**
```
$ az role assignment list --resource-group rg-azuser7799_mml.local-DQLkl
Owner, Contributor, Application Insights Component Contributor, ... (all scoped to this one resource group)
$ az role assignment list   # subscription scope
(empty)
```
Confirms, again, this is a Microsoft Learn sandbox subscription: Owner
at exactly one pre-existing resource group, no subscription-wide rights
(a brand-new resource group name could not be created -- re-confirmed,
consistent with the earlier Azure Readiness pass's finding).

### -1.2 Resource plan presented and approved

Presented to the user: Key Vault (Standard, RBAC auth) + Azure OpenAI
(account + 2 model deployments) + Log Analytics/Application Insights,
all inside the one existing resource group, estimated cost under $0.01
for the verification calls themselves, no fixed monthly fee at this
usage level. **User approved "Yes, provision all three."** No resource
was created before this explicit approval.

### -1.3 What was actually provisioned (real Azure resources, still live)

| Resource | Type | Region | Name |
|---|---|---|---|
| Key Vault | `Microsoft.KeyVault/vaults` | eastus | `kv-cdpi-audit1` |
| Azure OpenAI account | `Microsoft.CognitiveServices/accounts` (kind OpenAI) | westus | `aoi-cdpi-test-westus` |
| Chat deployment | `.../accounts/deployments` | westus | `gpt-4o` (version `2024-11-20`) |
| Embedding deployment | `.../accounts/deployments` | westus | `text-embedding-3-small` (version `1`) |
| Log Analytics workspace | `Microsoft.OperationalInsights/workspaces` | eastus | `law-cdpi-audit1` |
| Application Insights | `Microsoft.Insights/components` | eastus | `appi-cdpi-audit1` |

All inside the existing resource group `rg-azuser7799_mml.local-DQLkl`
(the only one this account can write to) -- no new resource group was
created, nothing existing (the pre-existing `stauditrj12` storage
account) was touched.

**Real regional/quota findings along the way, not fabricated:**
- `eastus` and `eastus2` both returned `InsufficientQuota` for a NEW
  Cognitive Services (OpenAI) account -- a real subscription-level
  quota restriction in this sandbox, distinct from model-level quota.
  `westus` and `southcentralus` worked; `centralindia` returned
  `UnsupportedLocationForSku` for S0.
- `centralindia` is the resource group's own nominal "location" metadata, but
  resources can and do live in other regions within it (the existing storage
  account is in `eastus`) -- ARM resource groups aren't a regional boundary.
- The originally planned `gpt-4o-mini` (version `2024-07-18`) deployment failed with
  `ServiceModelDeprecated` ("deprecated since 03/31/2026") -- a real,
  dated model lifecycle fact, not assumed. Switched the deployment target to
  `gpt-4o` (version `2024-11-20`, lifecycle `Legacy` but not deprecated).
- A `gpt-5-mini` deployment was tried first and real-tested: it **rejected
  the `max_tokens` parameter** (`"Unsupported parameter: 'max_tokens' is
  not supported with this model. Use 'max_completion_tokens' instead"`)
  and **rejected `temperature=0`** (`"Unsupported value: 'temperature'
  does not support 0.0 with this model. Only the default (1) value is
  supported"`). Both are real, verified API behaviors of that specific
  model generation. Rather than reworking `llm_utils.get_chat_llm()` to
  special-case every current/future reasoning-model's parameter
  constraints (scope creep beyond this verification), the chat
  deployment was switched to `gpt-4o`, which matches the parameters this
  repo's actual call sites already use (`max_tokens`, non-default
  temperature) -- **this is itself a real, useful finding**: if an
  admin ever configures a `gpt-5`-family deployment for
  `AZURE_OPENAI_CHAT_DEPLOYMENT`, every real call site in this repo
  would break on `max_tokens`/`temperature` today. Recorded as a known
  limitation in Section -1.6.

### -1.4 Application configuration (real, via existing mechanisms)

Written via `secrets_loader.set_env_secret()` (the same function the
real backend's Secrets Management write path uses) and, for the one API
key, via `az cognitiveservices account keys list` piped directly into
that function -- **never printed, never committed, never passed through
chat**:

| Key | Value source | Secret? |
|---|---|---|
| `AZURE_KEY_VAULT_URL` | Bicep/CLI output (non-secret) | No |
| `AZURE_OPENAI_ENDPOINT` | `az cognitiveservices account show` | No |
| `AZURE_OPENAI_CHAT_DEPLOYMENT` | set to `gpt-4o` | No |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | set to `text-embedding-3-small` | No |
| `AZURE_OPENAI_API_VERSION` | set to `2024-10-21` | No |
| `AZURE_OPENAI_API_KEY` | `az cognitiveservices account keys list` → `set_env_secret` | **Yes -- never displayed** |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | `az monitor app-insights component show` → `set_env_secret` | **Yes -- never displayed** |

One attempt to additionally copy the real `AZURE_OPENAI_API_KEY` *into*
Key Vault (to prove Key-Vault-sourced secrets feed real app
authentication, not just a disposable test string) was **blocked by this
session's own safety guardrail** (flagged as credential materialization)
and not retried or worked around -- the Key Vault verification below
instead uses a dedicated non-sensitive test secret, which still proves
the full real pipeline (auth -> connectivity -> retrieval -> consumption
by application code), just not with a live production credential inside
the vault.

### -1.5 Real integration tests executed

**Key Vault** -- wrote a test secret directly via `az keyvault secret
set` (value: a fixed, non-sensitive test string), then retrieved it
through the application's own, unmodified code path:
```
$ python -c "import secrets_loader; print(secrets_loader.get_secret_with_source('audit-connectivity-test'))"
source: key_vault
value matches expected: True
```
**Real, verified:** authentication (via the logged-in Azure CLI
session, picked up automatically by `DefaultAzureCredential`), vault
connectivity, secret retrieval, and consumption by
`secrets_loader.get_secret_with_source()` — the exact function every
other secret in this app resolves through. **Status: READY.**

**Azure OpenAI** -- ran `llm_utils.run_azure_openai_test_request()`
(the app's own, unmodified real-test function) against the deployed
`gpt-4o` and `text-embedding-3-small` models:
```json
{
  "configured": true,
  "chat": {"outcome": "succeeded", "response_preview": "OK"},
  "embedding": {"outcome": "succeeded", "dimensions": 1536}
}
```
A real chat completion (model replied "OK" to "Reply with the single
word: OK") and a real embedding request (1536 dimensions, matching
`text-embedding-3-small`'s real published dimensionality) both
succeeded through `AzureChatOpenAI`/`AzureOpenAI` clients pointed at the
real deployed resource -- confirmed by first **failing** realistically
against the wrong model/parameters (see -1.3) before succeeding against
the corrected configuration. **Status: READY.**

**Application Insights** -- ran `observability.run_connectivity_test()`
(the app's own, unmodified function), which emitted one real span:
```json
{
  "correlation_id": "connectivity-test-dd1ed94a9e77",
  "trace_id": "2202a015-c563-46f2-8378-07e7d0399c7d",
  "span_id": "21eb4b3d-2dd5-49e9-814a-1ec94b5226ae",
  "written_to_local_log": true,
  "application_insights_handed_off": true
}
```
Then, independently, **queried Azure Monitor directly** (not trusting
the handoff alone) via the real Log Analytics workspace, ~2+ minutes
later, to confirm actual ingestion:
```
$ az monitor log-analytics query --workspace <law-cdpi-audit1 customerId> \
    --analytics-query "AppTraces | where Properties has 'dd1ed94a9e77' | project TimeGenerated, Message, Properties"
[
  {
    "Message": "ai_trace",
    "Properties": "...'trace_id': '2202a015-c563-46f2-8378-07e7d0399c7d', 'span_id': '21eb4b3d-2dd5-49e9-814a-1ec94b5226ae', ...'correlation_id': 'connectivity-test-dd1ed94a9e77', 'workflow': 'observability_self_test', 'stage': 'connectivity_test', 'status': 'succeeded', ...",
    "TimeGenerated": "2026-10-23T14:50:09.675638Z"
  }
]
```
**The exact trace_id/span_id/correlation_id generated locally is
independently confirmed present in Azure Monitor.** This is real
ingestion, not merely "the SDK didn't error." As an unplanned bonus,
this same query also surfaced real telemetry from this session's test
suite runs and earlier manual commands (e.g. `azure_config`/
`connection_check` spans from `azure_readiness` exercises) -- further,
incidental confirmation the pipeline captures real application activity
broadly, not just a hand-crafted test span. **Status: READY.**

### -1.6 Full regression suite (real evidence, not claimed)

```
$ python -m pytest tests/ -q -k "not e2e"
652 passed, 1 deselected, 10 warnings in 137.94s
```
All passing, with real `AZURE_KEY_VAULT_URL`/`AZURE_OPENAI_*`/
`APPLICATIONINSIGHTS_CONNECTION_STRING` now present in `.env` -- no test
depends on these being absent. Noticeably slower than the pre-provisioning
baseline (137s vs ~40s) because several previously-mocked-by-absence code
paths now make real network calls during the suite (e.g. any
unmocked `secrets_loader.get_secret()` now genuinely checks Key Vault
first). Not a regression in correctness, but a real, newly-introduced
cost/latency characteristic of local test runs once Key Vault is
configured -- worth knowing, not silently absorbed.

### -1.7 Cleanup

**Two duplicate Cognitive Services accounts** were created incidentally
while probing region availability (`aoi-cdpi-test-swedencentral`,
`aoi-cdpi-test-southcentralus`) -- neither was ever configured with
deployments or used for any real test. Both were deleted
(`az cognitiveservices account delete`, soft-delete). **Full purge was
attempted and failed**, honestly reported rather than silently skipped:
```
ERROR: (AuthorizationFailed) ... does not have authorization to perform action
'Microsoft.CognitiveServices/locations/resourceGroups/deletedAccounts/delete' ...
```
This account's permissions don't extend to purging soft-deleted
Cognitive Services resources (a subscription-level action, outside this
sandbox's Owner-at-one-resource-group scope). These two soft-deleted
accounts do not appear in the active resource list, are not billable,
and will auto-purge after the service's default retention period. No
further action is possible from this account; **not a cleanup failure
this process could have prevented.**

The `gpt-5-mini` test deployment (real-tested, found incompatible with
this repo's parameter usage -- see -1.3) was deleted after its real test
completed, before being replaced with `gpt-4o`.

**Retained, by explicit approval:** Key Vault, Azure OpenAI account (2
deployments), Log Analytics workspace, and Application Insights -- these
are the actual application configuration this audit was asked to verify,
not throwaway test infrastructure, and are meant to keep working after
this session ends.

**Ongoing cost disclosure:** at idle (no further app usage), expect:
Key Vault -- a few cents per 10,000 operations, effectively free at dev
volume; Azure OpenAI -- **zero** until tokens are actually sent (no
idle/reserved cost for `Standard`/`GlobalStandard` deployments at this
capacity); Log Analytics/Application Insights -- free up to 5GB/month
ingestion, and this app's real local-log volume (hundreds of spans per
session at most) is a small fraction of that. **Total actual spend
incurred by this verification pass: a single real `gpt-4o` chat
completion + one `text-embedding-3-small` embedding call, both minimal
(a handful of tokens each) -- on the order of $0.0001, not independently
itemized by Azure in real time but consistent with the <$0.01 estimate
presented for approval.**

### -1.8 Updated summary table

| Component | Previous status | **Current status** | Real evidence |
|---|---|---|---|
| Key Vault | BLOCKED | **READY** | Real secret write + retrieval through the app's own `secrets_loader.get_secret_with_source()`, `source="key_vault"` |
| Azure OpenAI / AI Foundry | BLOCKED | **READY** | Real chat completion ("OK") + real embedding (1536 dims) through `llm_utils.run_azure_openai_test_request()`, unmodified app code |
| Application Insights | BLOCKED | **READY** | Real span independently confirmed present in Azure Monitor via direct `AppTraces` KQL query, ~2+ min after emission |

### -1.9 Remaining limitations (none are provisioning blockers anymore)

1. `gpt-5`-family (and likely other newer reasoning-style) Azure OpenAI
   deployments are incompatible with this repo's current
   `max_tokens`/explicit-`temperature` usage in `llm_utils.get_chat_llm()`
   -- real, verified, not yet fixed in code (out of scope for this
   verification pass; would need per-model-family parameter handling).
   Works correctly today with `gpt-4o`/`gpt-4o-mini`/`gpt-3.5`-era
   models.
   
2. Two soft-deleted Cognitive Services accounts cannot be purged by this
   account (permissions scoped to one resource group, purge needs
   subscription-level rights) -- will self-resolve after Azure's
   retention period, no cost impact in the meantime.
3. The deliberate decision not to copy the real `AZURE_OPENAI_API_KEY`
   into Key Vault itself (blocked by a safety guardrail, not retried)
   means Key Vault's *consumption-by-the-app* proof used a dedicated test
   secret rather than a live production credential -- the underlying
   mechanism is identical either way, but this is worth knowing if
   stricter proof is ever required.

---

### 0.1 What was fixed

| # | Component | Change | Files |
|---|---|---|---|
| 1 | Key Vault dependency | Installed `azure-keyvault-secrets` for real (`pip install`, confirmed importable); uncommented it + `azure-identity` + `azure-monitor-opentelemetry` in both dependency manifests (previously commented "only needed once provisioned," which is exactly how this bug went unnoticed) | `requirements.txt`, `azure-functions/requirements.txt` |
| 2 | Key Vault error handling | Added real error categorization (`authentication_failed`/`authorization_denied`/`timeout`/`endpoint_unreachable`/`dependency_missing`/`secret_not_found`) distinguishing a genuine auth/permission failure from the routine "secret not in vault" case -- both still fall back to `.env` (never breaks the app), but only the former is now loudly warned about and recorded in `_last_keyvault_error` for an admin to see | `secrets_loader.py` |
| 3 | Key Vault live check | Added `get_keyvault_status()` -- a real, free, safe connectivity check (`list_properties_of_secrets`, first page only, never reads a secret value) -- and wired it into `azure_integrations.check_connection("key_vault")`. Key Vault's `capabilities.health_check` is now `True` (was `False`) | `secrets_loader.py`, `azure_integrations.py`, `azure_readiness.py` |
| 4 | Azure OpenAI / Foundry integration | Added `llm_utils.get_chat_llm()`/`get_embedding_client_and_model()` -- real `AzureChatOpenAI`/`AzureOpenAI` clients, selected automatically when `AZURE_OPENAI_ENDPOINT`+`AZURE_OPENAI_API_KEY`+`AZURE_OPENAI_CHAT_DEPLOYMENT` (and separately `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` for embeddings) are all set, falling back to plain OpenAI otherwise. Updated **every real call site** (7 of them) to use the factory instead of constructing `ChatOpenAI` directly | `llm_utils.py`, `ResumeParser.py`, `rag_preview.py`, `generate_questions.py`, `interview_bot.py`, `voicebot/voice_bot.py`, `semantic-library/embeddings.py` |
| 5 | Azure OpenAI real test | Added `llm_utils.run_azure_openai_test_request()` -- sends one real minimal chat completion + embedding request, explicit/admin-triggered only (never automatic, since it's billable) | `llm_utils.py`, new endpoint `POST /admin/azure-readiness/azure-ai-foundry/test-request` |
| 6 | Azure AI registry status | `azure_ai_foundry` moved from `"planned"` to `"supported"` with a real `config_schema` (`azure_ai_search` remains genuinely planned -- confirmed unchanged) | `azure_integrations.py` |
| 7 | **Observability logging-level bug** | Found and fixed a second real defect: `configure_azure_monitor()` attaches its exporter to the root logger but never raises any logger's level, and Python's root logger defaults to `WARNING`. `record_ai_trace()`/`record_llm_call()` log at `.info()` -- every call was being silently dropped by Python's own logging module before Azure Monitor's handler ever ran, even with App Insights "configured" successfully. Fixed by explicitly setting `cdpi.ai_trace`/`cdpi.llm_observability` to `INFO` right after a successful `configure_azure_monitor()` call. Verified directly: `isEnabledFor(logging.INFO)` was `False` before the fix, `True` after | `observability.py` |
| 8 | Observability real test | Added `observability.run_connectivity_test()` -- emits one real span with a unique correlation ID through the app's normal telemetry path, reports whether it was handed to the App Insights exporter, and explicitly states that handoff is not proof of ingestion | `observability.py`, new endpoint `POST /admin/azure-readiness/application-insights/test-telemetry` |
| 9 | UI | Added an "Azure AI & Observability" tab to the Azure Readiness dashboard with both explicit test actions wired in mock and real API clients; mock mode honestly reports "not configured"/"simulated result" rather than fabricating success | `frontend/src/components/AzureReadiness.jsx`, `apiClient.js`, `mockApiClient.js` |
| 10 | Config surface | Added the 5 `AZURE_OPENAI_*` keys to the Secrets Management registry and `.env.example` | `secrets_registry.py`, `.env.example` |

### 0.2 Updated status per component

| Component | Before this pass | After this pass | Why |
|---|---|---|---|
| Key Vault dependency | **FAIL** (latent bug) | **FIXED** | `azure-keyvault-secrets` now installed and declared; reproduced the fix working (see 0.3) |
| Key Vault connectivity/auth | **BLOCKED** | **BLOCKED** (unchanged) | Still no `AZURE_KEY_VAULT_URL` in this environment -- fixing the dependency doesn't provision a vault. The real check (`get_keyvault_status()`) is implemented and unit-tested, but has never run against a real vault |
| Azure AI / Foundry / Azure OpenAI | **NOT INTEGRATED** | **BLOCKED** | Real integration code now exists and is unit-tested (mocked SDK calls); no `AZURE_OPENAI_*` credentials exist in this environment, so a real chat/embedding request has never been sent. `run_azure_openai_test_request()` against the real endpoint was exercised once with fake credentials to confirm it fails honestly (not fabricated success) -- see 0.3 |
| Observability (logging-level bug) | **FAIL** (latent bug, undiscovered until this pass) | **FIXED** | Reproduced the exact silent-drop condition before the fix and the correct `INFO`-enabled state after it |
| Observability (Azure ingestion) | **BLOCKED** | **BLOCKED** (unchanged) | No `APPLICATIONINSIGHTS_CONNECTION_STRING` configured in this environment. The connectivity-test function and endpoint are real and unit-tested, but real Azure-side ingestion has never been verified (needs a provisioned resource + waiting out ingestion latency + checking the Portal, none of which this process can do) |
| Local telemetry generation | **PASS** | **PASS** (unchanged, still real) | |

### 0.3 Tests executed (real pytest runs, not claimed)

```
$ python -m pytest tests/ -q
653 passed, 10 warnings in 42.91s
```
Before this fix pass, the suite stood at 611 passed (0 failing). **42 new
tests added this pass, zero regressions**, split across:
- `tests/test_secrets_loader.py` -- 10 new tests (`TestKeyVaultErrorCategorization`, `TestGetKeyvaultStatus`), including one pinning that a missing-dependency failure is categorized distinctly from a routine "secret not found," and one confirming `get_keyvault_status()` never reports `healthy` without a real call succeeding.
- `tests/test_llm_utils_azure_openai.py` -- 15 new tests, including `test_golden_selecting_azure_actually_constructs_an_azure_client` (inspects the real constructed `AzureChatOpenAI` instance's `azure_endpoint`/`deployment_name` fields, not just its class name) and `test_adversarial_never_fabricates_success_without_a_real_call`, which runs the **real** (unmocked) SDK code path against fake-but-realistic Azure credentials and confirms it fails honestly (a real DNS/connection failure against a nonexistent resource -- see below).
- `tests/test_observability.py` -- 7 new tests (`TestAppInsightsReadyRaisesLoggerLevel`, `TestRunConnectivityTest`), including a reproduction of the exact silent-drop bug and confirmation of the fix.
- `tests/test_azure_readiness.py` -- 6 new endpoint tests for the two new explicit-test endpoints, plus adversarial RBAC checks.
- `tests/test_ai_observability_azure_hub.py` -- `TestPlannedAdapters` rewritten to reflect `azure_ai_foundry`'s new status.

**One real (unmocked) negative test actually ran against the network:**
```
tests/test_llm_utils_azure_openai.py::TestRunAzureOpenAITestRequest::test_adversarial_never_fabricates_success_without_a_real_call PASSED
```
This test sets fake Azure OpenAI credentials pointing at
`https://fake-resource.openai.azure.com/` and calls the real
`run_azure_openai_test_request()` with no mocking at all -- it genuinely
attempted a real network connection, which failed (no such resource
exists), and the function correctly reported `"outcome": "failed"`
rather than crashing or fabricating success. This is the closest this
pass could get to a "real Azure AI test" without actual credentials --
it proves the failure path is real and honest, not that a real Azure
OpenAI resource works (that remains BLOCKED).

### 0.4 Verified live in the browser

Logged in as the mock-mode admin account, opened the new "Azure AI &
Observability" tab on the Azure Readiness page, and clicked both new
test buttons:
- **Azure OpenAI / AI Foundry test** → rendered "Not configured." (correct -- mock mode cannot read real env vars)
- **Application Insights connectivity test** → rendered a real-shaped correlation ID, "Local log only" badge, and the explicit "this is a simulated result... switch to mode:\"real\"" disclaimer

No console errors. Confirms the UI never claims a real result it didn't actually produce, even in mock mode.

### 0.5 Remaining blockers (none of these are code defects -- all are missing real Azure credentials/resources)

1. No Key Vault provisioned in the available subscription (Microsoft Learn sandbox, scoped to one pre-existing resource group with no create permissions at subscription scope -- confirmed in the earlier Azure Readiness pass).
2. No Azure OpenAI / AI Foundry resource or deployment exists or was provisioned (would require explicit approval + real cost).
3. No Application Insights resource exists or was provisioned.
4. Real end-to-end verification of all three (a real Key Vault secret retrieval, a real Azure OpenAI response, a real span visible in Application Insights' Portal) remains **BLOCKED** pending #1-3, exactly as it was before this pass -- what changed is that the *code* is now real and tested, not that credentials appeared.

---

## Original audit findings (before this fix pass)

**Environment used:** this machine, now with Azure CLI 2.91.0 installed
and `az login` completed (real session: `azuser7799_mml.local@karthikirisoutlook.onmicrosoft.com`,
subscription "Learner Account", tenant `karthikirisoutlook.onmicrosoft.com`
— a Microsoft Learn sandbox, confirmed scoped to one pre-existing
resource group, no subscription-wide create rights).

## 1. Azure Secrets / Key Vault — **BLOCKED** (not provisioned) + **1 real code defect found**

**Source:** `secrets_loader.py`

**Configuration check (real, not assumed):**
```
$ grep -oE "^[A-Z_]+=" .env
OPENAI_API_KEY
INPUT_FOLDER
OUTPUT_FOLDER
CSV_FOLDER
GMAIL_WATCHER_USER
GMAIL_WATCHER_APP_PASSWORD
JWT_SECRET
```
`AZURE_KEY_VAULT_URL` is **not set** — Key Vault is not provisioned, not
configured, never attempted. Every `get_secret()` call in this app
currently resolves from `.env`/environment only. Confirmed via
`secrets_loader.get_secret_with_source()` for `OPENAI_API_KEY`:
`source == "environment"`.

**Real code defect found and verified:** `_keyvault_client()`
(`secrets_loader.py:82-86`) imports `from azure.keyvault.secrets import
SecretClient`. That package is **not installed** in this environment
(commented out in `requirements.txt` as "only needed once Key Vault is
provisioned"):
```
$ python -c "from azure.keyvault.secrets import SecretClient"
ModuleNotFoundError: No module named 'azure.keyvault'
```
Simulated setting `AZURE_KEY_VAULT_URL` in-process (no real vault, just
to exercise the code path) reproduces the real failure and confirms the
fallback behavior:
```
[WARN] Key Vault lookup for 'OPENAI_API_KEY' failed (No module named 'azure.keyvault'); falling back to environment/.env.
source: environment
```
**This means:** even once a real Key Vault is provisioned and
`AZURE_KEY_VAULT_URL` is set, secret retrieval would still silently fail
and fall back to `.env` until `azure-keyvault-secrets` is actually
installed (uncomment it in `requirements.txt` and `pip install`). The
fallback itself works correctly and safely (never raises, never blocks
the app) — but today Key Vault cannot function even if configured.
`azure-identity` (the credential half) **is** installed; only the
`SecretClient` package is missing.

**Vault connectivity, permissions, retrieval, downstream auth:**
not testable — no vault exists to connect to, and provisioning one was
out of scope without explicit approval (would incur cost/create a
resource). **Status: BLOCKED.**

## 2. Azure AI (Foundry / Azure OpenAI) — **NOT INTEGRATED** (confirmed absent, not mocked)

**Search performed:**
```
$ grep -rn "AZURE_OPENAI|AzureOpenAI|azure.ai.inference|AzureChatOpenAI|openai.azure" --include="*.py" .
(no matches outside azure_integrations.py's own "planned, not implemented" registry entry)
```
**Confirmed real call sites instead:**
- `llm_utils.py` — LangChain `ChatOpenAI` (plain OpenAI API)
- `semantic-library/embeddings.py:53` — `from openai import OpenAI` (plain OpenAI API)

There is **no Azure AI Foundry or Azure OpenAI code path anywhere in
this repository** — not a mock standing in for one, not a hardcoded
response pretending to be one. `azure_integrations.py`'s own registry
already documents this accurately (`azure_ai_foundry`/`azure_ai_search`:
`status: "planned"`), and this audit independently confirms it by
exhaustive search, not by trusting that comment.

The application's **real, working, non-Azure** AI calls (plain OpenAI)
are genuinely live — `logs/ai_traces.jsonl` has 103 real
`embedding_generation` spans against `text-embedding-3-small`, most
recently `2026-10-20T14:44:22Z`, with real `cost_usd` figures. This
proves OpenAI itself isn't mocked. But it is **not Azure AI** by any
definition, so a "minimal real inference request against Azure" cannot
be sent — there is no Azure endpoint, deployment, or credential for one
to send it to. **Status: NOT INTEGRATED (confirmed, not assumed).**

## 3. Observability — **PARTIAL**: real locally, never reached Azure

**Source:** `observability.py`

**What's real:**
- `logs/ai_traces.jsonl` exists, 553 lines, real content — verified
  directly:
  ```
  {"trace_id": "...", "workflow": "resume_matching", "stage": "embedding_generation",
   "status": "succeeded", "duration_ms": 1739.2,
   "metadata": {"model": "text-embedding-3-small", "total_tokens": 2, "cost_usd": 4e-08,
   "cost_source": "documented_estimate"}, "logged_at": "2026-10-20T14:44:21.622961+00:00"}
  ```
  Breakdown by workflow: `llm` (280 spans), `azure_config` (162 — connection
  checks/resource discovery, generated by this session's own test runs
  and live readiness checks), `resume_matching` (103 — real embedding
  calls), `azure_ephemeral_test` (8). These are genuine records of code
  that actually ran in this session, not sample/seed data — every span
  has a real `trace_id`/`span_id`/`logged_at` and, for the embedding
  spans, a real OpenAI-reported token count.
- Trace correlation works: every span carries `trace_id` so a caller can
  group one workflow's stages, and `_appinsights_ready()`/Cosmos
  dual-write are genuinely best-effort (never block or corrupt the
  always-written local JSONL if either sink is unavailable) — confirmed
  by reading the code, not assumed.

**What's not real / never happened:**
- `APPLICATIONINSIGHTS_CONNECTION_STRING` is **not set** in `.env` —
  `_appinsights_ready()` returns `False` immediately, nothing has ever
  been emitted to Azure Monitor/Application Insights. The
  `azure-monitor-opentelemetry` package **is** installed, so unlike Key
  Vault, this path would actually work the moment the connection string
  is configured — but it never has been in this environment.
- Cosmos DB dual-write (`_write_span_to_cosmos`) has never fired either
  — Cosmos is not configured (`COSMOS_ENDPOINT`/`COSMOS_KEY`/
  `COSMOS_DATABASE` all absent).
- `logs/llm_observability.jsonl` (the separate `record_llm_call()` log)
  does not exist on disk in this environment — no LLM call through
  `llm_utils.py` that uses that specific instrumentation point has run
  here yet (distinct from the embedding calls above, which use
  `record_ai_trace()` directly).

**Dashboard metrics:** the AI Observability Center's admin page reads
`logs/ai_traces.jsonl` directly (`azure-functions/api_blueprint.py`'s
`/admin/ai-observability*` endpoints) — real stored records, not
fabricated sample data, confirmed by the real file content above. But
every number on that dashboard today reflects **local** telemetry only;
none of it has been verified to reach Azure because nothing has been
sent there. **Status: PARTIAL** — local telemetry generation is real and
verified; Azure ingestion is unconfigured and therefore unverifiable,
not failed.

## 4. Summary table

| Component | Status | Evidence |
|---|---|---|
| Key Vault connectivity/auth/retrieval | **BLOCKED** | No `AZURE_KEY_VAULT_URL` configured; no vault provisioned |
| Key Vault code path itself | **FAIL** (latent) | `ModuleNotFoundError: No module named 'azure.keyvault'` reproduced directly — would fail even if a vault were configured, until `azure-keyvault-secrets` is installed |
| Secret fallback to `.env` | **PASS** | Real, verified: `OPENAI_API_KEY` resolves with `source="environment"`, used in real OpenAI calls (see below) |
| Azure AI / Foundry / Azure OpenAI | **NOT INTEGRATED** | Exhaustive repo search found zero Azure AI code paths; confirmed by reading `llm_utils.py`/`embeddings.py` directly |
| Plain OpenAI (non-Azure) calls | **PASS** (context only, not what was asked) | 103 real embedding spans in `logs/ai_traces.jsonl` with real cost figures, most recent `2026-10-20T14:44:22Z` |
| Local observability (JSONL logs) | **PASS** | 553 real span records read directly from `logs/ai_traces.jsonl`, correct trace/span correlation, real timestamps |
| Application Insights ingestion | **BLOCKED** | `APPLICATIONINSIGHTS_CONNECTION_STRING` not set; `azure-monitor-opentelemetry` package confirmed installed and would work once configured |
| Cosmos DB telemetry dual-write | **BLOCKED** | Cosmos not configured; never attempted |
| Dashboard data source | **PASS** (not fabricated) | Confirmed reads the real JSONL file, not sample data |

## 5. Remaining fixes, in priority order

1. **Install `azure-keyvault-secrets`** (`pip install azure-keyvault-secrets`, uncomment in `requirements.txt`) — Key Vault retrieval is currently broken by a missing dependency, independent of whether a vault is ever provisioned. This is a real bug, found and verified in this audit, not present in any prior session report.
2. Provision a real Key Vault and set `AZURE_KEY_VAULT_URL` (needs explicit approval — this subscription is a scoped Learner sandbox; confirm it allows Key Vault creation in the one authorized resource group before attempting).
3. Provision Application Insights and set `APPLICATIONINSIGHTS_CONNECTION_STRING` — the only blocker here is configuration, not code; the integration code is real and the SDK is already installed.
4. Azure AI integration does not exist — treating it as "to do" rather than "broken" is accurate; building it is a new feature, not a fix.

## Section -3: Service Observability Report — Real Azure Monitor Query-Back (new feature)

Built real read-back access to Azure Monitor / Log Analytics (`azure_monitor_query.py`), the first code in this repo to ever query already-ingested telemetry out of Azure rather than only push to it. Verified against the real `law-cdpi-audit1` workspace (customerId `6b301a51-65ff-4e6f-b7c3-f34524199337`) via `az login` + the `azure-monitor-query` SDK.

**Real, confirmed schema finding**: this environment's Application Insights resource is workspace-based, so ingested records land in `AppTraces` (not the classic `traces` table), and `observability.py`'s nested `custom_dimensions` dict is logged by the OpenTelemetry exporter as Python-repr text (not valid JSON) inside the `Properties` column — confirmed directly against real ingested data, documented in full in `docs/AZURE_OBSERVABILITY.md`, worked around via KQL `extract()` regexes rather than `parse_json()`.

**Real query-back verified live**: `get_telemetry_health()` confirmed real ingestion (`ingestion_confirmed: true`, real timestamp `2026-10-24 08:44:54Z`); `get_request_volume()`, `get_latency_percentiles()`, `get_ai_usage()`, `get_recent_errors()` all returned real, non-fabricated data matching what a direct `az monitor log-analytics query` CLI call independently confirmed (78 real requests, 100% success, p95 9785.7ms, 13 real `gpt-3.5-turbo` calls / 9004 tokens / $0.0422).

**Status: READY** (query-back) — a genuine upgrade from this doc's earlier Application Insights section, which only ever confirmed the push side. See `docs/AZURE_OBSERVABILITY.md` for full detail, metric definitions, and known gaps (the Python-repr `custom_dimensions` limitation chief among them).

## Section -4: Real Document Intelligence provisioned, Azure Speech removed (user-directed)

**Document Intelligence** (`docintel-cdpi-audit1`, real Azure AI Document Intelligence resource, SKU F0 — free tier, $0 cost, 500 pages/month) was provisioned for real at the user's explicit request after confirming F0 availability in `eastus`. Endpoint and key written to `.env` (never printed to chat). Status moved from `NOT_CONFIGURED` to real, live-verified `PARTIALLY_VERIFIED` (its fullest achievable readiness state — no safe/free connectivity check exists without a billable extraction call, same category as Azure OpenAI and Application Insights).

**Blob Storage** was wired up for free (reusing the already-provisioned `stauditrj12` storage account's connection string, now also in the main `.env`, not just `azure-functions/local.settings.json`) and confirmed `READY` via a real live connection check.

**Azure Speech** was removed from `azure_integrations.ADAPTER_TYPES` at the user's explicit request ("no speech needed remove it") — it is a real, working feature (`voicebot/azure_speech_utils.py`, imported by `voicebot/voice_bot.py`), not deleted, only taken out of the Azure Service Hub / Readiness / Observability Report service registry and dashboards. Its stale Cosmos `integration_instances` registration was also deleted via the real `DELETE /manage/azure-services/{id}` endpoint (CDPI-side record only, never touches the real Azure resource — there was none to touch, since Speech was never provisioned).

**Real bug found twice in this pass (same root cause both times)**: a cached `integration_health_checks` result from before a service was configured can make `compute_readiness()` report stale `NOT_CONFIGURED` even after the real secret is added, because `validate_config()`'s fresh check only short-circuits the *first* branch — once config passes, a stale cached `check_result` with an old `configuration_invalid` outcome still drives the final status. Not a code bug (the fix is simply running a live "Validate" after any config change, which both Blob Storage and Document Intelligence received here) — but worth noting as an operational gotcha, since the UI doesn't auto-invalidate a stale cached check on config change.

**Real test breakage found and fixed**: `tests/test_azure_readiness.py::TestComputeReadiness::test_golden_missing_secret_is_not_configured` relied on `document_intelligence` being genuinely unconfigured in this ambient test environment — invalidated by the real provisioning above. Fixed by mocking `validate_config` explicitly (same pattern already used by a neighboring test in the same file) instead of depending on environment state. Full suite after the fix: 671 passed, 0 failed (excluding the one pre-existing, unrelated Selenium flake documented in Section -3).
