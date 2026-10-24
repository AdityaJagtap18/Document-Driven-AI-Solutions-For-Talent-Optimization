"""Centralized Azure Integration Readiness: composes azure_integrations.py's
real config validation + connection-check vocabulary and az_cli.py's real
Azure CLI authentication detection into ONE status per registered
integration instance, using the documented 9-state vocabulary below.

This module invents no new verification -- it classifies evidence that
already exists (azure_integrations.validate_config/check_connection,
az_cli.get_account_status) into a single readiness status, so "READY"
here means exactly what it means in those lower-level functions: a real,
live, free round-trip succeeded for integrations with a safe connectivity
check (Cosmos DB, Blob Storage, Key Vault), or configuration is confirmed
present for integrations where no further safe/free verification exists
(Document Intelligence, Azure Speech, Application Insights) --
never fabricated beyond what was actually run.

READINESS STATUSES (exactly one per instance):
  NOT_INTEGRATED        -- this repo has no code for the adapter type yet.
  NOT_CONFIGURED        -- a required secret/setting is missing.
  AUTHENTICATION_REQUIRED -- no usable authentication context (either a
                           live check failed with an auth error, or the
                           integration depends on Azure CLI/managed
                           identity auth and no CLI session exists).
  PERMISSION_DENIED     -- authenticated, but the operation was denied.
  CONNECTION_FAILED     -- the service could not be reached.
  PARTIALLY_VERIFIED    -- configuration confirmed present, but no
                           further safe/free check exists for this
                           adapter (so functional readiness is unproven,
                           not assumed).
  READY                 -- config, auth, and the deepest safe/free
                           connectivity check available all succeeded.
  DEGRADED              -- works, but with a known caveat (reserved;
                           none of today's checks currently produce it).
  UNKNOWN                -- no check has been run yet for this instance.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import azure_integrations  # noqa: E402
import az_cli  # noqa: E402

READINESS_STATUSES = (
    "NOT_INTEGRATED", "NOT_CONFIGURED", "AUTHENTICATION_REQUIRED", "PERMISSION_DENIED",
    "CONNECTION_FAILED", "PARTIALLY_VERIFIED", "READY", "DEGRADED", "UNKNOWN",
)

STATUS_DEFINITIONS = {
    "NOT_INTEGRATED": "This repository has no implementation for this Azure service yet.",
    "NOT_CONFIGURED": "Required configuration (a secret or setting) is missing.",
    "AUTHENTICATION_REQUIRED": "No usable authentication context is available.",
    "PERMISSION_DENIED": "Authentication succeeded, but the operation was denied.",
    "CONNECTION_FAILED": "The configured service could not be reached, or the connection failed.",
    "PARTIALLY_VERIFIED": "Configuration is present, but no further safe, free functional check exists for this integration.",
    "READY": "Configuration, authentication, permissions, and the deepest safe connectivity check available all succeeded.",
    "DEGRADED": "The integration works, but with a known operational problem.",
    "UNKNOWN": "No validation has been run yet, or available evidence is insufficient to determine the state.",
}

# Adapters whose only supported auth method is Azure CLI / managed
# identity -- for these, a missing local `az login` session IS the
# blocking factor (there's no API key fallback to fall back on), so
# readiness should say AUTHENTICATION_REQUIRED rather than the more
# generic PARTIALLY_VERIFIED a key-based adapter gets when it has no
# live check.
_CLI_DEPENDENT_ADAPTERS = {"key_vault"}


def compute_readiness(adapter_type: str, check_result: dict = None, az_authenticated: bool = None) -> dict:
    """check_result: the most recent azure_integrations.check_connection()
    result for this instance, or None if no check has ever been run.
    az_authenticated: az_cli.get_account_status()["authenticated"], or
    None if not evaluated (treated as "unknown/unauthenticated" for
    _CLI_DEPENDENT_ADAPTERS -- fails closed, never assumes authenticated)."""
    adapter = azure_integrations.get_adapter(adapter_type)
    if adapter is None:
        return {"status": "UNKNOWN", "reason": f"Unknown adapter type '{adapter_type}'."}

    if azure_integrations.is_planned(adapter_type):
        return {"status": "NOT_INTEGRATED", "reason": adapter["description"]}

    config_errors = azure_integrations.validate_config(adapter_type)
    if config_errors:
        return {"status": "NOT_CONFIGURED", "reason": config_errors[0]}

    if check_result is None:
        # No real check has run yet -- for an adapter with no fallback
        # auth method (key_vault), a missing local az login is a useful,
        # specific guess at WHY a check would fail. Once a real
        # check_result exists (below), its actual outcome always wins --
        # key_vault now has a real health check (secrets_loader.
        # get_keyvault_status()), so this heuristic never overrides real
        # evidence, only fills the gap before any evidence exists.
        if adapter_type in _CLI_DEPENDENT_ADAPTERS and not az_authenticated:
            return {
                "status": "AUTHENTICATION_REQUIRED",
                "reason": "This integration's only supported auth method is Azure CLI/managed identity, "
                          "and no active Azure CLI session was detected. Run 'az login'.",
            }
        return {"status": "UNKNOWN", "reason": "No validation has been run yet for this integration."}

    outcome = check_result.get("outcome")
    category = check_result.get("error_category")
    detail = check_result.get("detail", "")

    if outcome == "healthy":
        return {"status": "READY", "reason": detail or "Live connectivity check succeeded."}
    if outcome == "degraded":
        return {"status": "DEGRADED", "reason": detail or "Connectivity check succeeded with a caveat."}
    if outcome == "unknown":
        if category == "configuration_invalid":
            return {"status": "NOT_CONFIGURED", "reason": detail}
        return {"status": "PARTIALLY_VERIFIED", "reason": detail or "Configuration confirmed present; no further safe check exists."}
    if outcome == "unhealthy":
        if category == "authentication_failed":
            return {"status": "AUTHENTICATION_REQUIRED", "reason": detail}
        if category == "authorization_denied":
            return {"status": "PERMISSION_DENIED", "reason": detail}
        return {"status": "CONNECTION_FAILED", "reason": detail}
    return {"status": "UNKNOWN", "reason": f"Unrecognized check outcome '{outcome}'."}


def readiness_for_instance(instance: dict, latest_check: dict = None, az_account_status: dict = None) -> dict:
    """Full readiness record for one registered IntegrationInstance
    (repositories.py's integration_instances shape), combining
    compute_readiness() with the instance's own metadata -- the shape
    the readiness dashboard/API returns per service."""
    adapter_type = instance["adapter_type"]
    adapter = azure_integrations.get_adapter(adapter_type) or {}
    az_authenticated = (az_account_status or {}).get("authenticated")
    verdict = compute_readiness(adapter_type, check_result=latest_check, az_authenticated=az_authenticated)

    missing_config = []
    if not azure_integrations.is_planned(adapter_type):
        presence = azure_integrations.secret_presence(adapter_type)
        missing_config = [k for k, present in presence.items() if not present]

    return {
        "integration_id": instance.get("id"),
        "adapter_type": adapter_type,
        "service_name": adapter.get("name", adapter_type),
        "status": verdict["status"],
        "status_definition": STATUS_DEFINITIONS[verdict["status"]],
        "reason": verdict["reason"],
        "environment": instance.get("environment"),
        "display_name": instance.get("display_name"),
        "enabled": instance.get("enabled"),
        "missing_configuration": missing_config,
        "last_validated_at": (latest_check or {}).get("checked_at"),
        "last_successful_validation_at": (latest_check or {}).get("checked_at") if (latest_check or {}).get("outcome") == "healthy" else None,
        "resource_identifiers": {
            "subscription_id": instance.get("config", {}).get("subscription_id"),
            "resource_group": instance.get("config", {}).get("resource_group"),
        } if instance.get("config") else {},
        "validated_against": "real" if (latest_check or {}).get("outcome") in ("healthy", "unhealthy", "degraded") else "config_only",
        "recommended_next_action": recommend_next_action(verdict["status"], adapter_type),
    }


def recommend_next_action(status: str, adapter_type: str) -> str:
    if status == "NOT_INTEGRATED":
        return "Not applicable -- this service requires new application code, not configuration."
    if status == "NOT_CONFIGURED":
        return "Set the missing secret(s)/setting(s) via the Secrets Management console or .env, then re-validate."
    if status == "AUTHENTICATION_REQUIRED":
        if adapter_type in _CLI_DEPENDENT_ADAPTERS:
            return "Run 'az login' locally, then re-validate."
        return "Check the configured credential is still valid, then re-validate."
    if status == "PERMISSION_DENIED":
        return "Grant the minimum required role/permission to the credential or identity in use, then re-validate."
    if status == "CONNECTION_FAILED":
        return "Check network reachability and the configured endpoint, then re-validate."
    if status == "PARTIALLY_VERIFIED":
        return "No further safe/free check exists for this integration -- this is its fullest achievable readiness state."
    if status == "READY":
        return "No action needed."
    if status == "UNKNOWN":
        return "Run a validation check."
    return "Review the reason above."
