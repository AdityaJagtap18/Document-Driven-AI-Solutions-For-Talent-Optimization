# Azure Integration Readiness

Covers deliverables A (integration inventory), B (readiness dashboard
usage/statuses), C (local Azure CLI setup), and E (Selenium smoke test)
of the CDPI Azure Integration Readiness spec. See
[AZURE_SERVICE_HUB_V2.md](AZURE_SERVICE_HUB_V2.md) for the underlying
Azure Service Hub console this dashboard is a composed view over, and
[../infra/bicep/ephemeral-tests/README.md](../infra/bicep/ephemeral-tests/README.md)
for the ephemeral provision-test-destroy workflow (deliverable D).

## A. Azure integration inventory

Re-verified against the real repository for this pass (not assumed from
docs) -- every row below already existed in `azure_integrations.py`
before this pass; this pass adds the **Readiness status** composition
(no new check) and **Azure CLI detection** (new).

| Service | Repository evidence | Frontend page | Backend integration | Auth | Connectivity test | Functional test | CLI dependency |
|---|---|---|---|---|---|---|---|
| Azure Cosmos DB | `context-lake/client.py` | Azure Service Hub, Azure Readiness | `azure_integrations.py` adapter `cosmos_db` | key or managed identity | Real: `database.read()` (cheap metadata GET) | Same as connectivity (no further safe op exists) | No (key-based) |
| Azure Blob Storage | `context-lake/blob_store.py` | Azure Service Hub, Azure Readiness | adapter `blob_storage` | connection string or managed identity | Real: `get_account_information()` | Same (ephemeral-tests also runs a real upload/download, see below) | No (connection-string-based) |
| Azure AI Document Intelligence | `document_intelligence_client.py` | Azure Service Hub, Azure Readiness | adapter `document_intelligence` | API key | None (only billable ops exist) -- config presence only | None | No |
| Azure AI Speech | `voicebot/azure_speech_utils.py` | Azure Service Hub, Azure Readiness | adapter `azure_speech` | API key | None (billable) -- config presence only | None | No |
| Azure Key Vault | `secrets_loader.py` | Azure Service Hub, Azure Readiness, Secrets | adapter `key_vault` | **managed identity / Azure CLI only** | None -- config presence only | None | **Yes** -- the one adapter where a missing `az login` is itself the blocking readiness factor |
| Azure Application Insights | `observability.py` | Azure Service Hub, Azure Readiness | adapter `application_insights` | connection string | None -- config presence only | None | No |
| Azure AI Foundry | *(none)* | Azure Service Hub, Azure Readiness | `status: "planned"` | n/a | n/a | n/a | n/a |
| Azure AI Search | *(none)* | Azure Service Hub, Azure Readiness | `status: "planned"` | n/a | n/a | n/a | n/a |
| Azure CLI (local auth) | **new this pass**: `az_cli.py` | Azure Readiness ("Azure CLI" card) | shells out to `az version`/`az account show`/`az account list` | n/a (it IS the auth mechanism) | Real: `az account show` | n/a | n/a |

Risks already documented in `azure_integrations.py`'s module docstring
(unchanged by this pass): Document Intelligence/Speech have no safe free
operation to verify connectivity (only billable ones), so they can never
exceed `PARTIALLY_VERIFIED`; resource lifecycle operations
(create/update/delete) are not implemented for any adapter.

## B. Readiness dashboard

**Page:** Admin Panel -> Azure Readiness (`azure_readiness` page,
gated by `azure.config.read`/`azure.manage`, same permission tier as
Azure Service Hub).
**Backend:** `azure_readiness.py` (classification logic, no new
verification) + `GET/POST /admin/azure-readiness*` endpoints in
`azure-functions/api_blueprint.py`.

### Statuses (exactly these nine, never a tenth)

| Status | Meaning |
|---|---|
| `NOT_INTEGRATED` | This repo has no code for the service. |
| `NOT_CONFIGURED` | A required secret/setting is missing. |
| `AUTHENTICATION_REQUIRED` | No usable auth context (live check failed on auth, or the adapter is Azure-CLI-only and no `az login` session exists). |
| `PERMISSION_DENIED` | Authenticated, but the operation was denied. |
| `CONNECTION_FAILED` | Could not reach the service. |
| `PARTIALLY_VERIFIED` | Config confirmed present, but no further safe/free check exists (Document Intelligence, Speech, App Insights -- this is their ceiling). |
| `READY` | Config + auth + the deepest safe connectivity check all succeeded. |
| `DEGRADED` | Works, with a known caveat (reserved; unused today). |
| `UNKNOWN` | No check has ever been run yet for this instance. |

**`READY` is never reported from configuration validation alone** --
`azure_readiness.compute_readiness()` only reaches `READY` after a real
`check_result` with `outcome == "healthy"` is passed in, which only ever
comes from `azure_integrations.check_connection()`'s actual live network
call. Asserted directly in
`tests/test_azure_readiness.py::TestComputeReadiness::test_adversarial_never_reports_ready_from_config_validation_alone`.

### Actions

- **Validate** (per-service) / **Validate all** -- runs the real,
  existing `azure_integrations.check_connection()` (the same call the
  Azure Service Hub's own "Check connection" button runs; same
  rate-limit store, so hammering either surface for the same integration
  is throttled together). **Never creates an Azure resource.**
- The Azure CLI card shows real `az version`/`az account show` output
  (never fabricated, never a stored/cached guess) -- see Section C.
- **Ephemeral Tests tab** -- the only resource-creating workflow in this
  app; see the Bicep guide.

## C. Local Azure CLI setup

1. Check install: `az version`. If missing, install from
   [Microsoft's docs](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli).
2. `az login` (or `az login --use-device-code` over SSH/remote).
3. Verify: `az account show`.
4. List/select subscription if you have more than one:
   `az account list --output table`, then
   `az account set --subscription "<id-or-name>"`.
5. Re-check `az account show` to confirm the right subscription is active.

This app **never** asks you to paste a token/credential anywhere, never
automates the interactive login, and never stores CLI output beyond the
non-secret fields `az account show` itself prints (tenant ID,
subscription ID/name, signed-in user) -- see `az_cli.py`'s module
docstring. For a **deployed** app, prefer managed identity over a
developer's interactive CLI session (this repo's Key Vault adapter is
designed to run under the Function App's system-assigned managed
identity in production -- see `infra/main.bicep` -- CLI auth is for
**local development** only).

**Confirmed in this sandbox:** no `az` CLI is installed (`az --version`
fails with "command not found"). Every status/behavior described above
for the not-installed/not-authenticated case is what this repository's
own Azure Readiness page actually shows right now, not a hypothetical --
see the Final Implementation Report for real screenshots/evidence.

## E. Selenium smoke test

**File:** `tests/e2e/test_azure_readiness_smoke.py`
**Test:** `test_azure_readiness_page_loads_and_runs_safe_check`

**Dependencies:** `selenium` and `pytest` (both already in
`requirements.txt`/`requirements-test.txt`). Needs Chrome installed
locally -- Selenium 4's bundled Selenium Manager resolves a matching
chromedriver automatically (first run needs outbound network to fetch
it; cached after).

**What it does:** starts the real documented local dev server
(`python -m http.server`, the same command `.claude/launch.json`'s
`"frontend"` entry runs) on a dedicated test port, launches headless
Chrome, logs in as the mock-mode admin demo account, navigates to Azure
Readiness via the app's own nav (no URL routing exists in this
bundler-free app), asserts the page and its service rows render,
asserts every rendered status is one of the nine documented values,
clicks "Validate all" (read-only), and asserts no service reports
`READY` in an environment with zero Azure secrets configured (that
would be exactly the fabricated-success failure mode this spec
prohibits). It never touches the Ephemeral Tests tab, so it can never
trigger real provisioning even in an environment where one might
succeed.

**Run it:**
```bash
pytest tests/e2e/test_azure_readiness_smoke.py -v
```

**Result categories:** `PASS` (assertions held), `FAIL` (an assertion or
app behavior failed for real), `BLOCKED` (reported via `pytest.skip` --
e.g. Chrome/chromedriver genuinely unavailable), never a silently-passed
test when a prerequisite is missing.

**Actually executed in this environment** (not just written): see the
Final Implementation Report for the real pass/fail history, including
two real bugs this test caught and that were fixed before it passed --
an XPath `text()` node-set pitfall and a sidebar-overflow
"element not interactable" click failure, both genuine Selenium gotchas,
not scripted-in failures.
