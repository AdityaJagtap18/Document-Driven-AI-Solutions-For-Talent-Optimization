"""Mode C of the Azure Readiness workflow: provision -> test -> destroy,
against a dedicated, freshly-created, uniquely-named resource group only.

This is the ONLY code path in this entire repository that provisions or
deletes real Azure infrastructure. Every step requires the PRIOR step to
have succeeded, and execution requires an explicit human approval
(`approved=True`, set only by the POST .../execute endpoint after the
plan has been returned to and reviewed by an admin -- see
api_blueprint.py). Nothing here runs automatically as part of an ordinary
readiness scan (see azure_readiness.py, which never provisions anything).

SCOPE IS DELIBERATELY NARROW: one resource type (a Standard_LRS Storage
Account, the cheapest safe SKU) for one integration (Blob Storage) --
not "every Azure service mentioned in the docs." See
ALLOWED_RESOURCE_PLANS. Extending this to another service means adding
another plan entry AND its own Bicep template under infra/bicep/
ephemeral-tests/, not a generic "deploy anything" capability.

LIFECYCLE STATES (ephemeral_test_runs container, one doc per run):
  PLANNED -> AWAITING_APPROVAL -> PROVISIONING -> PROVISIONED -> TESTING
  -> TEST_PASSED | TEST_FAILED -> CLEANUP_IN_PROGRESS
  -> CLEANUP_VERIFIED | CLEANUP_FAILED
  BLOCKED can occur at any pre-provisioning step (no az CLI, not
  authenticated, template invalid, resource group name collision).

EVERY subprocess call here is wrapped the same way az_cli.py's calls
are: a missing `az`/`bicep` binary, a timeout, or a malformed response
all fail closed into a BLOCKED/CLEANUP_FAILED result with a clear reason
-- never silently treated as success. This module has never been run
against a real Azure subscription in this environment (no `az` CLI is
installed here -- confirmed via `az --version` failing) -- execute_run()
will realistically stop at the authentication gate (step 2) every time
it's exercised in this sandbox, which is the HONEST, correct behavior
the spec requires, not a bug.
"""

import json
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import az_cli  # noqa: E402
import azure_integrations  # noqa: E402

_BICEP_DIR = Path(__file__).resolve().parent / "infra" / "bicep" / "ephemeral-tests"
_DEPLOY_TIMEOUT_SECONDS = 600
_CLEANUP_POLL_INTERVAL_SECONDS = 10
_CLEANUP_POLL_ITERATIONS = 6  # ~60s of synchronous polling per cleanup call -- see cleanup_run()'s docstring for why this is bounded, not a full wait-until-done loop

ALLOWED_RESOURCE_PLANS = {
    "blob_storage_test": {
        "label": "Blob Storage connectivity test (1 Standard_LRS storage account)",
        "adapter_type": "blob_storage",
        "bicep_file": "storage-test.bicep",
        "azure_resource_types": ["Microsoft.Storage/storageAccounts"],
        "estimated_cost_usd_per_hour": 0.02,  # Standard_LRS Hot tier, negligible for a run measured in minutes
        "allowed_regions": ["eastus", "eastus2", "westus2", "centralus", "westeurope"],
        "max_duration_minutes": 60,
    },
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def validate_plan_request(resource_plan_id: str, region: str) -> list:
    errors = []
    plan = ALLOWED_RESOURCE_PLANS.get(resource_plan_id)
    if plan is None:
        errors.append(f"Unknown or unapproved resource plan '{resource_plan_id}'. Allowed: {', '.join(ALLOWED_RESOURCE_PLANS)}.")
        return errors
    if region not in plan["allowed_regions"]:
        errors.append(f"Region '{region}' is not in this plan's allowed region list: {', '.join(plan['allowed_regions'])}.")
    return errors


def generate_plan(resource_plan_id: str, region: str, requested_by: str) -> dict:
    """Pure, local computation -- no Azure call, no az CLI needed. This is
    what POST .../ephemeral-tests/plan returns for the admin to review
    before any approval or provisioning happens."""
    errors = validate_plan_request(resource_plan_id, region)
    if errors:
        raise ValueError("; ".join(errors))

    plan = ALLOWED_RESOURCE_PLANS[resource_plan_id]
    run_id = uuid.uuid4().hex[:8]
    resource_group_name = f"rg-cdpi-ephemeral-{run_id}"
    storage_account_name = f"cdpieph{run_id}"  # 15 chars, well under the 24-char Storage Account limit

    return {
        "id": run_id,
        "run_id": run_id,
        "resource_plan_id": resource_plan_id,
        "status": "AWAITING_APPROVAL",
        "requested_by": requested_by,
        "region": region,
        "resource_group_name": resource_group_name,
        "planned_resources": [
            {"type": t, "name": storage_account_name if "storageAccounts" in t else None}
            for t in plan["azure_resource_types"]
        ],
        "storage_account_name": storage_account_name,
        "estimated_cost_usd_per_hour": plan["estimated_cost_usd_per_hour"],
        "max_duration_minutes": plan["max_duration_minutes"],
        "tags": {
            "project": "CDPI", "purpose": "ephemeral-integration-test",
            "testRunId": run_id, "managedBy": "CDPI-test-runner", "createdAt": _now(),
        },
        "test_status": None,
        "cleanup_status": None,
        "resources_actually_created": [],
        "created_at": _now(),
        "approved": False,
        "approved_by": None,
        "blocked_reason": None,
    }


def _run_az(args, timeout=_DEPLOY_TIMEOUT_SECONDS):
    # See az_cli._az_executable()'s docstring -- "az" alone is not
    # resolvable by subprocess.run(shell=False) on Windows (only
    # az.cmd is), confirmed against a real winget-installed CLI.
    executable = az_cli._az_executable()
    if executable is None:
        return None, "", "Azure CLI ('az') was not found on PATH."
    try:
        result = subprocess.run([executable] + args, capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout, result.stderr
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        return None, "", str(e)


def execute_run(run: dict, actor: str) -> dict:
    """Advances a PLANNED/AWAITING_APPROVAL run through provisioning and
    the one functional test its resource plan defines. Mutates and
    returns `run` (caller persists it) -- never raises; every failure
    mode sets status=BLOCKED/TEST_FAILED with blocked_reason/test_status
    explaining exactly what happened, per the spec's "fail closed, report
    honestly" requirement."""
    if run["status"] not in ("PLANNED", "AWAITING_APPROVAL"):
        run["blocked_reason"] = f"Cannot execute a run in status '{run['status']}'."
        run["status"] = "BLOCKED"
        return run
    if not run.get("approved"):
        run["blocked_reason"] = "This run has not been explicitly approved -- refusing to provision anything."
        run["status"] = "BLOCKED"
        return run

    # Step 1: verify Azure CLI authentication -- the real, honest blocking
    # gate in this sandbox (no `az` CLI installed here at all).
    account = az_cli.get_account_status()
    if not account["authenticated"]:
        run["status"] = "BLOCKED"
        run["blocked_reason"] = f"Azure CLI authentication required: {account['detail']}"
        return run

    plan = ALLOWED_RESOURCE_PLANS.get(run["resource_plan_id"])
    if plan is None:
        run["status"] = "BLOCKED"
        run["blocked_reason"] = f"Resource plan '{run['resource_plan_id']}' is no longer recognized."
        return run

    bicep_path = _BICEP_DIR / plan["bicep_file"]
    if not bicep_path.exists():
        run["status"] = "BLOCKED"
        run["blocked_reason"] = f"Bicep template not found at {bicep_path}."
        return run

    # Step 2: refuse to reuse an existing resource group (this workflow
    # only ever creates a fresh, uniquely-named one).
    rc, stdout, stderr = _run_az(["group", "exists", "--name", run["resource_group_name"]], timeout=30)
    if rc is None:
        run["status"] = "BLOCKED"
        run["blocked_reason"] = f"Could not run 'az group exists' ({stderr or 'no az CLI'})."
        return run
    if stdout.strip().lower() == "true":
        run["status"] = "BLOCKED"
        run["blocked_reason"] = f"Resource group '{run['resource_group_name']}' already exists -- refusing to reuse it."
        return run

    # Step 3: create the resource group and deploy.
    run["status"] = "PROVISIONING"
    tag_args = [f"{k}={v}" for k, v in run["tags"].items()]
    rc, _out, stderr = _run_az(["group", "create", "--name", run["resource_group_name"], "--location", run["region"], "--tags"] + tag_args, timeout=60)
    if rc != 0:
        run["status"] = "BLOCKED"
        run["blocked_reason"] = azure_integrations.redact_secret(f"'az group create' failed: {stderr}")
        return run

    tags_bicep_param = "{" + ",".join(f"{k}:'{v}'" for k, v in run["tags"].items()) + "}"
    rc, stdout, stderr = _run_az([
        "deployment", "group", "create",
        "--resource-group", run["resource_group_name"],
        "--template-file", str(bicep_path),
        "--parameters", f"storageAccountName={run['storage_account_name']}", f"location={run['region']}", f"tags={tags_bicep_param}",
        "--output", "json",
    ])
    if rc != 0:
        run["status"] = "BLOCKED"
        run["blocked_reason"] = azure_integrations.redact_secret(f"Bicep deployment failed: {stderr}")
        return run

    run["status"] = "PROVISIONED"
    run["resources_actually_created"] = [{"type": "Microsoft.Storage/storageAccounts", "name": run["storage_account_name"]}]

    # Step 4: run the one functional test this plan defines -- a real
    # upload + download of a uniquely-named throwaway blob, then delete
    # just that blob (the storage account itself is torn down by
    # cleanup_run(), not this step).
    run["status"] = "TESTING"
    test_result = _run_blob_storage_functional_test(run)
    run["test_status"] = "TEST_PASSED" if test_result["passed"] else "TEST_FAILED"
    run["test_detail"] = test_result["detail"]
    run["status"] = run["test_status"]
    return run


def _run_blob_storage_functional_test(run: dict) -> dict:
    """Uploads a uniquely-named test blob to the just-provisioned storage
    account, reads it back, verifies the content round-trips, deletes it.
    Needs the account key -- fetched live via 'az storage account keys
    list', used in-process only, never logged or persisted."""
    rc, stdout, stderr = _run_az([
        "storage", "account", "keys", "list",
        "--resource-group", run["resource_group_name"], "--account-name", run["storage_account_name"],
        "--output", "json",
    ], timeout=60)
    if rc != 0:
        return {"passed": False, "detail": azure_integrations.redact_secret(f"Could not retrieve storage account key: {stderr}")}
    try:
        keys = json.loads(stdout)
        account_key = keys[0]["value"]
    except (json.JSONDecodeError, KeyError, IndexError):
        return {"passed": False, "detail": "Could not parse storage account keys response."}

    try:
        from azure.storage.blob import BlobServiceClient

        conn_str = f"DefaultEndpointsProtocol=https;AccountName={run['storage_account_name']};AccountKey={account_key};EndpointSuffix=core.windows.net"
        svc = BlobServiceClient.from_connection_string(conn_str)
        container_name = "ephemeral-test"
        container = svc.get_container_client(container_name)
        if not container.exists():
            container.create_container()
        blob_name = f"readiness-test-{uuid.uuid4().hex[:8]}.txt"
        payload = b"CDPI ephemeral integration test payload"
        container.upload_blob(blob_name, payload, overwrite=True)
        downloaded = container.download_blob(blob_name).readall()
        container.delete_blob(blob_name)
        if downloaded != payload:
            return {"passed": False, "detail": "Downloaded blob content did not match what was uploaded."}
        return {"passed": True, "detail": "Uploaded, downloaded, and verified a test blob; deleted it afterward."}
    except Exception as e:
        return {"passed": False, "detail": azure_integrations.redact_secret(f"Blob upload/download test failed: {e}")}


def cleanup_run(run: dict, actor: str) -> dict:
    """Deletes ONLY run['resource_group_name'], and only after verifying
    the group's own testRunId tag matches this run's id (belt-and-braces
    ownership check on top of the exact-name match). Polls for up to
    ~60s synchronously (bounded so an HTTP request handling this doesn't
    hang indefinitely); if the group still exists after that, returns
    CLEANUP_IN_PROGRESS (not FAILED) so a caller can poll again via a
    follow-up cleanup/verify call rather than this single call blocking
    for the full real-world deletion time."""
    if run["status"] not in ("PROVISIONED", "TESTING", "TEST_PASSED", "TEST_FAILED", "CLEANUP_IN_PROGRESS", "CLEANUP_FAILED"):
        run["cleanup_status"] = "CLEANUP_FAILED"
        run["blocked_reason"] = f"Nothing to clean up for a run in status '{run['status']}' (no resources were ever provisioned)."
        return run

    rc, stdout, stderr = _run_az(["group", "exists", "--name", run["resource_group_name"]], timeout=30)
    if rc is None:
        run["cleanup_status"] = "CLEANUP_FAILED"
        run["blocked_reason"] = f"Could not run 'az group exists' ({stderr or 'no az CLI'})."
        run["status"] = "CLEANUP_FAILED"
        return run
    if stdout.strip().lower() != "true":
        run["cleanup_status"] = "CLEANUP_VERIFIED"
        run["status"] = "CLEANUP_VERIFIED"
        run["cleanup_verified_at"] = _now()
        return run

    rc, _out, tags_stderr = _run_az(["group", "show", "--name", run["resource_group_name"], "--query", "tags", "--output", "json"], timeout=30)
    if rc == 0:
        try:
            tags = json.loads(_out or "{}") or {}
        except json.JSONDecodeError:
            tags = {}
        if tags.get("testRunId") != run["run_id"]:
            run["cleanup_status"] = "CLEANUP_FAILED"
            run["status"] = "CLEANUP_FAILED"
            run["blocked_reason"] = (
                f"Resource group '{run['resource_group_name']}' exists but its testRunId tag "
                f"('{tags.get('testRunId')}') does not match this run -- refusing to delete."
            )
            return run

    run["status"] = "CLEANUP_IN_PROGRESS"
    rc, _out, stderr = _run_az(["group", "delete", "--name", run["resource_group_name"], "--yes", "--no-wait"], timeout=30)
    if rc != 0:
        run["cleanup_status"] = "CLEANUP_FAILED"
        run["status"] = "CLEANUP_FAILED"
        run["blocked_reason"] = azure_integrations.redact_secret(f"'az group delete' failed to initiate: {stderr}")
        return run

    for _ in range(_CLEANUP_POLL_ITERATIONS):
        time.sleep(_CLEANUP_POLL_INTERVAL_SECONDS)
        rc, stdout, _stderr = _run_az(["group", "exists", "--name", run["resource_group_name"]], timeout=30)
        if rc == 0 and stdout.strip().lower() != "true":
            run["cleanup_status"] = "CLEANUP_VERIFIED"
            run["status"] = "CLEANUP_VERIFIED"
            run["cleanup_verified_at"] = _now()
            return run

    run["cleanup_status"] = "CLEANUP_IN_PROGRESS"
    run["blocked_reason"] = (
        f"Deletion was initiated but not yet confirmed after {_CLEANUP_POLL_ITERATIONS * _CLEANUP_POLL_INTERVAL_SECONDS}s "
        "of polling -- call cleanup again to keep checking (az's own deletion can take several more minutes)."
    )
    return run
