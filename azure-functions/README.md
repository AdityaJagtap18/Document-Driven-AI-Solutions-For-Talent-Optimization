# Event-Driven Pipeline + API Layer (Azure Functions)

Two things live here, both Azure Functions in the same app:

1. **Blob trigger** ([`function_app.py`](function_app.py)) — replaces `file_watcher.py`'s 60-second polling loop. A resume lands in the `raw-resumes` container, this fires immediately, no polling delay.
2. **HTTP API** ([`api_blueprint.py`](api_blueprint.py)) — the API layer Phase 4's frontend calls. Built after a gap was found: the roadmap assumed an API existed after Phase 2-3, but those phases only built the data layer and observability, nothing HTTP-callable. 7 endpoints: `GET /health`, `GET /candidates`, `GET /candidates/{id}`, `GET /candidates/{id}/interviews`, `GET /candidates/by-email/{email}`, `POST /candidates/{id}/review`, `GET /observability/summary`.

## Status: written and tested at the function-logic level, NOT deployed or run under a real Functions host

No Azure Functions Core Tools or Azure CLI are available in the environment this was built in, so:

- **Validated**: Both files were actually imported and executed against the real `azure-functions` SDK (not just read against documentation) — confirmed all 8 functions (7 HTTP + 1 blob trigger) register correctly via `app.register_functions(api_bp)`. The HTTP endpoints go further: each was actually *called* directly (`health(request)`, etc.) with constructed `func.HttpRequest` objects, and the full suite now has 15 unit tests (`tests/test_api_blueprint.py`) covering success, validation errors, not-found vs. backend-unavailable (503 vs. 404 — see the fix below), and field-filtering on the candidate-facing endpoint. `observability/summary` was verified against a real `logs/llm_observability.jsonl` produced by an actual `ResumeParser.py` run.
- **NOT validated**: anything that requires a running Functions host or a real Azure deployment — whether the blob trigger actually fires on a real upload, whether bindings resolve correctly against real Storage/Cosmos accounts, whether routing/auth behave identically under the real Functions runtime (different execution sandbox than direct Python function calls), CORS behavior against a real browser.
- **Bug found and fixed via this testing, not just written and assumed correct**: `context-lake/repositories.get_candidate()` had a bare `except Exception: return None`, which silently converted "Cosmos DB isn't configured" into the same response as "this candidate doesn't exist" — a 503-worthy condition indistinguishable from a 404. Now catches only `CosmosResourceNotFoundError` specifically; everything else (including the config `RuntimeError`) propagates so the API layer can return the correct status code. Confirmed via a before/after test run, not just reasoned about.

**Deployment caveat, not yet resolved**: this Function App imports from outside its own folder — `observability.py` and `secrets_loader.py` at the repo root, `repositories.py`/`client.py` in `context-lake/`, and the blob trigger shells out to `ResumeParser.py` at the repo root too. A standard `func azure functionapp publish` typically zips only the `azure-functions/` folder's own contents, which would NOT include these. Either the deployment needs to bundle the whole repo (not just this folder), or these shared modules need to be copied/symlinked into `azure-functions/` as part of a build step — not solved here, flagged as real follow-up work before a real deploy is attempted.

Treat this as a reviewed, logic-tested draft — not a verified-in-production component. `file_watcher.py` is left completely untouched — it's still the real, working, tested local path. This is an alternative deployment target to try once Phase 2's Storage account exists, not a replacement shipped in its place.

## Known limitation (not fixed here, by design — see function_app.py's docstring)

`ResumeParser.py` processes every PDF in `InputResume_Folder` per run, not just the one that triggered the function. Concurrent blob uploads within the same processing window will each spawn a full `ResumeParser.py` subprocess reprocessing the whole folder — a race condition (duplicate CSV rows, at minimum) under concurrent load. `file_watcher.py` already has this same property today via polling, so this isn't a regression, just not improved either. A real fix needs a queue between the trigger and the worker, or refactoring `ResumeParser.py` to process exactly one given file — tracked as a follow-up, not done in this pass.

## Local testing (once you have Azure Functions Core Tools)

```bash
cd azure-functions
pip install -r requirements.txt
cp local.settings.json.example local.settings.json   # fill in real connection strings
func start
```

Also needs the root `requirements.txt` installed (same Python environment, or accessible to it) since the function shells out to `python ResumeParser.py` in the repo root.

## Auth on the HTTP endpoints

`health` and `candidates/by-email/{email}` are anonymous (candidates/health checks don't have a function key). Everything else requires a function key (`?code=...` query param or `x-functions-key` header) — Azure-managed, zero extra setup, but **not** the Entra ID RBAC the roadmap calls for eventually. See `api_blueprint.py`'s module docstring for what upgrading to real Entra ID auth would need. Also note `candidates/by-email/{email}` has a real, documented gap: nothing stops looking up a different candidate's status by guessing their email — needs a confirmation-code mechanism before this is production-safe for real candidate data.

## Deploying for real

1. Provision an Azure Function App (Consumption plan is fine at this scale) in the same resource group as Phase 2's Storage account — see `docs/azure-provisioning/phase2-cosmos-storage-keyvault.md` for the pattern; a Function App provisioning doc/Bicep addition is a reasonable next step once this has been locally validated with `func start` first.
2. Resolve the deployment-packaging caveat above (this folder's imports reach outside itself) before `func azure functionapp publish` will actually work.
3. Set `BLOB_CONNECTION_STRING`, `COSMOS_ENDPOINT`/`COSMOS_KEY`/`COSMOS_DATABASE`, and the other secrets `ResumeParser.py` needs (`OPENAI_API_KEY`, etc., or `AZURE_KEY_VAULT_URL` if Key Vault is set up) as Application Settings on the Function App.
4. Deploy with `func azure functionapp publish <function-app-name>`.
5. Upload a test PDF to the `raw-resumes` container and confirm the blob trigger fires; hit `GET /api/health` (anonymous) and `GET /api/candidates?code=<function-key>` to confirm the HTTP side is live (check the Function App's logs / Application Insights — this is also where `observability.py`'s per-call token/cost records would show up, once `APPLICATIONINSIGHTS_CONNECTION_STRING` is set).

None of steps 1-5 has been exercised in this session — they're the actual verification this component still needs.
