"""Azure CLI local-authentication detection -- `az version`/`az account
show`, parsed into a safe, structured result. Never logs or returns raw
CLI stdout/stderr unredacted (account show can echo tenant/subscription
IDs, which aren't secrets, but never a token) and never stores or
forwards a token. This module only SHELLS OUT to a CLI the user runs
`az login` with themselves (see docs/AZURE_CLI_SETUP.md) -- it never
performs or automates the interactive login itself, per the explicit
"never ask the user to paste credentials" / "never automate interactive
auth" requirement.

Both functions fail closed: no `az` binary, a timeout, or a parse error
all come back as a clear, honest "not available" result -- never raise
out to a caller, since this is read-only diagnostic data for an admin
dashboard, not something that should ever 500 a page.
"""

import json
import shutil
import subprocess

_TIMEOUT_SECONDS = 10


def _az_executable():
    """Resolves the real `az` executable path via shutil.which(), which
    respects PATHEXT -- required on Windows, where the official installer
    puts only `az.cmd` (and a POSIX-only `az` shell script) on PATH, never
    a bare Windows-executable `az`. subprocess.run(["az", ...]) with
    shell=False does NOT do PATHEXT resolution itself (that's a cmd.exe
    shell behavior), so calling it with the literal string "az" silently
    raises FileNotFoundError on a real, working Windows install -- caught
    directly while building this module against a real `az` installed via
    winget. shutil.which() is cross-platform: finds plain `az` on
    Linux/Mac, `az.cmd` on Windows, without needing shell=True (which
    would reopen other injection concerns)."""
    return shutil.which("az")


def _run_az(args):
    """Returns (returncode, stdout, stderr) or None if `az` isn't on PATH
    or the call couldn't be made at all (timeout, OS error)."""
    executable = _az_executable()
    if executable is None:
        return None
    try:
        result = subprocess.run(
            [executable] + args, capture_output=True, text=True, timeout=_TIMEOUT_SECONDS,
        )
        return result.returncode, result.stdout, result.stderr
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return None
    except OSError:
        return None


def check_az_installed() -> dict:
    """{"installed": bool, "version": str|None, "detail": str}."""
    run = _run_az(["version", "--output", "json"])
    if run is None:
        return {"installed": False, "version": None, "detail": "Azure CLI ('az') was not found on PATH."}
    returncode, stdout, _stderr = run
    if returncode != 0:
        return {"installed": False, "version": None, "detail": "Azure CLI was found but 'az version' failed."}
    try:
        data = json.loads(stdout)
        version = data.get("azure-cli")
    except (json.JSONDecodeError, AttributeError):
        version = None
    return {"installed": True, "version": version, "detail": "Azure CLI is installed."}


def get_account_status() -> dict:
    """{"authenticated": bool, "tenant_id": str|None, "subscription_id": str|None,
    "subscription_name": str|None, "user": str|None, "detail": str}.

    tenant_id/subscription_id/user are not secrets (the same values
    `az account show` prints to any terminal) -- never includes an
    access token, refresh token, or any field `az account show`
    doesn't itself already surface."""
    installed = check_az_installed()
    if not installed["installed"]:
        return {
            "authenticated": False, "tenant_id": None, "subscription_id": None,
            "subscription_name": None, "user": None,
            "detail": "Azure CLI is not installed -- see docs/AZURE_CLI_SETUP.md for installation instructions.",
        }

    run = _run_az(["account", "show", "--output", "json"])
    if run is None:
        return {
            "authenticated": False, "tenant_id": None, "subscription_id": None,
            "subscription_name": None, "user": None,
            "detail": "Could not run 'az account show' (timeout or OS error).",
        }
    returncode, stdout, _stderr = run
    if returncode != 0:
        # The real, common case: `az` is installed but no one has run
        # `az login` yet (or the session expired). az's own stderr says
        # this plainly ("Please run 'az login'"); never echoed verbatim
        # since it isn't needed and could vary by CLI version/locale.
        return {
            "authenticated": False, "tenant_id": None, "subscription_id": None,
            "subscription_name": None, "user": None,
            "detail": "No active Azure CLI session. Run 'az login' (or 'az login --use-device-code') to authenticate.",
        }
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return {
            "authenticated": False, "tenant_id": None, "subscription_id": None,
            "subscription_name": None, "user": None,
            "detail": "'az account show' returned output this app couldn't parse.",
        }
    return {
        "authenticated": True,
        "tenant_id": data.get("tenantId"),
        "subscription_id": data.get("id"),
        "subscription_name": data.get("name"),
        "user": (data.get("user") or {}).get("name"),
        "detail": "Authenticated via Azure CLI.",
    }


def list_resource_group_resources(resource_group: str) -> dict:
    """{"resources": [{"name","type","location","status"}], "detail": str}
    -- real `az resource list` scoped to one resource group. Empty list
    (never an error) when not authenticated or the group is unreachable,
    so a caller can render "unavailable" rather than crash."""
    account = get_account_status()
    if not account["authenticated"]:
        return {"resources": [], "detail": account["detail"]}
    run = _run_az(["resource", "list", "--resource-group", resource_group, "--output", "json"])
    if run is None:
        return {"resources": [], "detail": "Could not run 'az resource list'."}
    returncode, stdout, stderr = run
    if returncode != 0:
        return {"resources": [], "detail": f"'az resource list' failed: {(stderr or '').strip()[:300]}"}
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return {"resources": [], "detail": "'az resource list' returned output this app couldn't parse."}
    resources = [
        {"name": r.get("name"), "type": r.get("type"), "location": r.get("location"),
         "status": (r.get("provisioningState") or "Unknown")}
        for r in data
    ]
    return {"resources": resources, "detail": f"{len(resources)} real resource(s) in '{resource_group}'."}


def get_cost_summary(resource_group: str) -> dict:
    """Real month-to-date cost, grouped by resource, scoped to one resource
    group -- via `az rest` hitting the Cost Management Query API directly
    (the `costmanagement` CLI extension's installed version has no `query`
    subcommand in this environment). Never fabricates a total: a failed or
    unauthorized call comes back as {"available": False, "detail": ...},
    never a zero/placeholder total.

    {"available": bool, "currency": str|None, "total": float|None,
     "by_resource": [{"name","type","cost"}], "detail": str}"""
    account = get_account_status()
    if not account["authenticated"]:
        return {"available": False, "currency": None, "total": None, "by_resource": [], "detail": account["detail"]}
    subscription_id = account["subscription_id"]
    uri = (
        f"https://management.azure.com/subscriptions/{subscription_id}"
        f"/resourceGroups/{resource_group}/providers/Microsoft.CostManagement/query"
        f"?api-version=2023-11-01"
    )
    body = json.dumps({
        "type": "ActualCost",
        "timeframe": "MonthToDate",
        "dataset": {
            "granularity": "None",
            "aggregation": {"totalCost": {"name": "PreTaxCost", "function": "Sum"}},
            "grouping": [{"type": "Dimension", "name": "ResourceId"}],
        },
    })
    run = _run_az(["rest", "--method", "post", "--uri", uri, "--body", body])
    if run is None:
        return {"available": False, "currency": None, "total": None, "by_resource": [],
                "detail": "Could not run 'az rest' against the Cost Management API."}
    returncode, stdout, stderr = run
    if returncode != 0:
        return {"available": False, "currency": None, "total": None, "by_resource": [],
                "detail": f"Cost Management query failed: {(stderr or '').strip()[:300]}"}
    try:
        data = json.loads(stdout)
        columns = [c["name"] for c in data["properties"]["columns"]]
        cost_idx = columns.index("PreTaxCost")
        resource_idx = columns.index("ResourceId")
        currency_idx = columns.index("Currency")
        rows = data["properties"]["rows"]
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return {"available": False, "currency": None, "total": None, "by_resource": [],
                "detail": "Cost Management API returned output this app couldn't parse."}
    currency = rows[0][currency_idx] if rows else None
    by_resource = [
        {"name": row[resource_idx].rsplit("/", 1)[-1], "type": row[resource_idx].split("/providers/")[-1].rsplit("/", 2)[0] if "/providers/" in row[resource_idx] else None,
         "cost": round(row[cost_idx], 4)}
        for row in rows
    ]
    by_resource.sort(key=lambda r: r["cost"], reverse=True)
    total = round(sum(row[cost_idx] for row in rows), 4)
    return {
        "available": True, "currency": currency, "total": total, "by_resource": by_resource,
        "detail": f"Real month-to-date cost from Azure Cost Management, {len(rows)} resource(s) billed in '{resource_group}'.",
    }


def list_subscriptions() -> dict:
    """{"subscriptions": [{"id","name","tenant_id","is_default"}], "detail": str}
    -- empty list (never an error) when not authenticated, so a caller
    can render "none" rather than crash."""
    account = get_account_status()
    if not account["authenticated"]:
        return {"subscriptions": [], "detail": account["detail"]}
    run = _run_az(["account", "list", "--output", "json"])
    if run is None:
        return {"subscriptions": [], "detail": "Could not run 'az account list'."}
    returncode, stdout, _stderr = run
    if returncode != 0:
        return {"subscriptions": [], "detail": "'az account list' failed."}
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return {"subscriptions": [], "detail": "'az account list' returned output this app couldn't parse."}
    subs = [
        {"id": s.get("id"), "name": s.get("name"), "tenant_id": s.get("tenantId"), "is_default": bool(s.get("isDefault"))}
        for s in data
    ]
    return {"subscriptions": subs, "detail": f"{len(subs)} subscription(s) visible to the signed-in account."}
