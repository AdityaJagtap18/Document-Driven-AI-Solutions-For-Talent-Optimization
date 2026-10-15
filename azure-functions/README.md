# Event-Driven Pipeline (Azure Functions)

Replaces `file_watcher.py`'s 60-second polling loop with a genuine Blob-triggered Azure Function — a resume lands in the `raw-resumes` container, this fires immediately, no polling delay.

## Status: written and API-validated, NOT deployed or tested

No Azure Functions Core Tools or Azure CLI are available in the environment this was built in, so:

- **Validated**: [`function_app.py`](function_app.py) was actually imported and executed with the real `azure-functions` SDK installed (not just read against documentation) — confirmed `@app.blob_trigger(...)` registers correctly and the `FunctionApp` recognizes exactly one function.
- **NOT validated**: anything that requires a running Functions host or a real Azure deployment — whether the blob trigger actually fires on a real upload, whether the `BLOB_CONNECTION_STRING` binding resolves correctly against a real Storage account, whether `subprocess.run(["python", "ResumeParser.py"], ...)` behaves correctly inside the Function's actual execution sandbox (different working directory assumptions, different Python environment than this dev machine).

Treat this as a reviewed draft. `file_watcher.py` is left completely untouched — it's still the real, working, tested local path. This is an alternative deployment target to try once Phase 2's Storage account exists, not a replacement shipped in its place.

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

## Deploying for real

1. Provision an Azure Function App (Consumption plan is fine at this scale) in the same resource group as Phase 2's Storage account — see `docs/azure-provisioning/phase2-cosmos-storage-keyvault.md` for the pattern; a Function App provisioning doc/Bicep addition is a reasonable next step once this has been locally validated with `func start` first.
2. Set `BLOB_CONNECTION_STRING` (and the other secrets `ResumeParser.py` needs — `OPENAI_API_KEY`, etc., or `AZURE_KEY_VAULT_URL` if Key Vault is set up) as Application Settings on the Function App.
3. Deploy with `func azure functionapp publish <function-app-name>`.
4. Upload a test PDF to the `raw-resumes` container and confirm the function fires (check the Function App's logs / Application Insights — this is also where `observability.py`'s per-call token/cost records would show up, once `APPLICATIONINSIGHTS_CONNECTION_STRING` is set).

None of step 1-4 has been exercised in this session — they're the actual verification this component still needs.
