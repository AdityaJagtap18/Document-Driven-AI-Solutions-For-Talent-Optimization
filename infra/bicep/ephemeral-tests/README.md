# Ephemeral Azure Integration Testing (Mode C)

Provision -> test -> destroy, against a dedicated, freshly-created
resource group only. This is the ONLY workflow in this repository that
can create or delete real Azure infrastructure -- see
[`ephemeral_test_runner.py`](../../../ephemeral_test_runner.py)'s module
docstring for the full safety model.

**Status: implemented, UNTESTED against a real subscription.** This
sandbox has no `az` CLI installed (confirmed: `az --version` fails with
"command not found"), so `execute_run()` has only ever been exercised
here with a mocked `az` CLI (`tests/test_ephemeral_test_runner.py`) and,
against this environment's real absence of the CLI, honestly stops at
the authentication gate every time -- see the Final Implementation
Report for the actual evidence.

## Approved resource types

**Exactly one, on purpose** -- not every Azure service mentioned in the
project docs (the spec's own explicit instruction):

| Plan ID | Resource | SKU | Est. cost | Allowed regions | Max duration |
|---|---|---|---|---|---|
| `blob_storage_test` | 1x Storage Account (`Microsoft.Storage/storageAccounts`) | `Standard_LRS`, StorageV2, Hot tier | ~$0.02/hour | eastus, eastus2, westus2, centralus, westeurope | 60 min |

Extending this to another service means adding another entry to
`ephemeral_test_runner.ALLOWED_RESOURCE_PLANS` **and** its own Bicep
template here -- never a generic "deploy anything" capability.

## Files

- [`storage-test.bicep`](storage-test.bicep) -- the template. Non-secret
  outputs only (names/IDs); the storage account key is fetched live via
  `az storage account keys list` at test time, used in-process, never
  logged or persisted.
- [`storage-test.parameters.json`](storage-test.parameters.json) --
  parameter shell with placeholder values (no secrets ever belong here).
- [`deploy.ps1`](deploy.ps1) / [`cleanup.ps1`](cleanup.ps1) -- manual
  command-line equivalents of what the Azure Readiness dashboard's
  Ephemeral Tests tab does programmatically (see below) -- useful for
  running the workflow without the web app, or for debugging it.

## Workflow (via the Azure Readiness dashboard)

**Plan -> Approve -> Execute -> (test runs automatically) -> Cleanup**

1. **Plan** (`POST /admin/azure-readiness/ephemeral-tests/plan`) --
   pure local computation, generates a unique run ID, resource group
   name (`rg-cdpi-ephemeral-<runId>`), and tags. **No Azure call, no
   resource created.**
2. **Approve** (`POST .../ephemeral-tests/{runId}/approve`) -- explicit
   human action. `execute` refuses to run without it.
3. **Execute** (`POST .../ephemeral-tests/{runId}/execute`) -- the ONLY
   step that can create a resource. In order: verify Azure CLI
   authentication (fails closed -> `BLOCKED` if not authenticated) ->
   refuse to reuse an existing resource group -> create the resource
   group + deploy the Bicep template -> run the one functional test
   (upload, download, verify, delete a throwaway blob) -> record
   `TEST_PASSED`/`TEST_FAILED`.
4. **Cleanup** (`POST .../ephemeral-tests/{runId}/cleanup`) -- verifies
   the resource group's `testRunId` tag matches this run before deleting
   anything, initiates `az group delete --yes --no-wait`, polls for up
   to ~60 seconds to confirm actual deletion (not just that the delete
   request was accepted), and reports `CLEANUP_VERIFIED` or
   `CLEANUP_IN_PROGRESS`/`CLEANUP_FAILED` -- call again to keep polling
   if still in progress.

## Safety guarantees (enforced in code, not just documented)

- Every run gets its **own** resource group, created fresh -- `execute`
  refuses to proceed if a group with that exact name already exists.
- Cleanup checks the group's `testRunId` tag matches the run before
  deleting -- refuses otherwise, even if the name matches.
- Cleanup only ever targets the **exact** resource group name for that
  run -- never a prefix pattern, never "all ephemeral groups," never a
  subscription-wide operation.
- `execute` requires `run["approved"] is True`, set only by a separate,
  explicit `approve` call -- generating a plan never implies consent to
  provision it.
- Every subprocess failure (missing `az`, timeout, non-zero exit) sets
  `status=BLOCKED`/`cleanup_status=CLEANUP_FAILED` with a specific
  `blocked_reason` -- never silently retried as success, never hidden.
- Test status and cleanup status are tracked as **separate fields** --
  a `TEST_PASSED` run with `CLEANUP_FAILED` is never reported as a fully
  successful lifecycle.

## Manual CLI usage (without the web app)

```powershell
cd infra/bicep/ephemeral-tests
$runId = -join ((97..122) | Get-Random -Count 8 | % {[char]$_})  # 8 lowercase chars
./deploy.ps1 -RunId $runId -Location eastus
# ... run your own test against the provisioned storage account ...
./cleanup.ps1 -RunId $runId
```

Both scripts are marked `*** UNTESTED ***` in their own headers for the
same reason as the dashboard's backend path: no Azure CLI exists in the
environment this was built in to run them against a real subscription.
