"""Single entry point for reading secrets: tries Azure Key Vault first (if
AZURE_KEY_VAULT_URL is set), falls back to the environment / .env
otherwise. This is additive and fully backward compatible — until
AZURE_KEY_VAULT_URL is set in .env, every call behaves exactly like
os.environ.get() did before Phase 2, so existing scripts keep working
unchanged whether or not Key Vault is provisioned yet.

Usage (replaces `os.environ.get("OPENAI_API_KEY")`):
    from secrets_loader import get_secret
    api_key = get_secret("OPENAI_API_KEY")
"""

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv, set_key

_ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(_ENV_PATH if _ENV_PATH.exists() else None)


_last_keyvault_error = {}  # vault_url -> {"category": str, "detail": str, "at": iso str} -- sanitized, never a secret value, for Secrets Management / Azure Readiness to surface a REAL auth/permission failure distinctly from "not provisioned" rather than only a printed line a UI never sees.


def _record_keyvault_error(vault_url: str, category: str, detail: str) -> None:
    from datetime import datetime, timezone

    _last_keyvault_error[vault_url] = {"category": category, "detail": detail, "at": datetime.now(timezone.utc).isoformat()}


def get_secret(name: str, default=None):
    """Looks up `name` in Key Vault first (if AZURE_KEY_VAULT_URL is set in
    the environment), then falls back to os.environ. A Key Vault failure
    never raises out of this function — it falls through to the
    environment, so a misconfigured Key Vault degrades to today's
    .env-only behavior instead of breaking the script that called this.
    It is NOT silently hidden, though: every failure is printed with its
    real category (see _categorize_keyvault_error below) and recorded in
    _last_keyvault_error for get_keyvault_status() to surface to an
    admin -- "never raises" and "never hidden" are different promises,
    and this function keeps both."""
    value, _source = get_secret_with_source(name, default)
    return value


def _categorize_keyvault_error(exception: Exception) -> str:
    """Same error vocabulary azure_integrations._categorize_error() uses
    for every other Azure adapter (authentication_failed/
    authorization_denied/endpoint_unreachable/timeout/service_unavailable/
    unknown_error) plus 'secret_not_found', which is NOT a failure (the
    vault is configured and reachable; this specific secret just isn't
    stored there) -- distinguishing that from a real connectivity/auth
    problem is the whole point of this function existing."""
    from azure.core.exceptions import ClientAuthenticationError, HttpResponseError, ResourceNotFoundError, ServiceRequestError

    if isinstance(exception, ResourceNotFoundError):
        return "secret_not_found"
    if isinstance(exception, ClientAuthenticationError):
        return "authentication_failed"
    if isinstance(exception, HttpResponseError) and getattr(exception, "status_code", None) == 403:
        return "authorization_denied"
    message = str(exception).lower()
    if "timed out" in message or "timeout" in message:
        return "timeout"
    if any(h in message for h in ("name or service not known", "failed to establish a new connection", "getaddrinfo failed", "connection refused")):
        return "endpoint_unreachable"
    if isinstance(exception, ServiceRequestError):
        return "service_unavailable"
    return "unknown_error"


def get_secret_with_source(name: str, default=None):
    """Same resolution as get_secret(), but also returns WHICH source
    answered it: ("value", "key_vault"), ("value", "environment"), or
    (default, None) if neither has it. Exists for the Secrets Management
    admin page (secrets_registry.py), which needs to show an
    administrator where each secret is actually being read from without
    a second, separately-maintained lookup implementation — get_secret()
    itself is defined in terms of this function, not the other way
    around, so there is exactly one resolution path."""
    vault_url = os.environ.get("AZURE_KEY_VAULT_URL")
    if vault_url:
        try:
            value = _get_from_keyvault(vault_url, name)
            if value is not None:
                return value, "key_vault"
            # SecretClient.get_secret() found a secret document with an
            # empty value -- genuinely rare, but still "found," so this
            # does NOT fall through to the category-logging path below.
        except ImportError as e:
            # The one failure mode that is a REAL CODE DEFECT, not an
            # Azure-side problem -- azure-keyvault-secrets isn't
            # installed. Never conflated with "not configured" or "no
            # access": those are expected states for an unprovisioned
            # vault; a missing package is a bug, logged as one.
            category, detail = "dependency_missing", str(e)
            print(f"[ERROR] Key Vault lookup for '{name}' failed: required package not installed ({e}). "
                  f"Run 'pip install azure-keyvault-secrets' (already in requirements.txt). Falling back to environment/.env.")
            _record_keyvault_error(vault_url, category, detail)
        except Exception as e:
            category = _categorize_keyvault_error(e)
            detail = str(e)
            if category == "secret_not_found":
                # Expected, routine case: the vault is configured and
                # reachable, this secret just lives in .env instead (or
                # nowhere yet) -- no warning-level noise for this.
                pass
            else:
                print(f"[WARN] Key Vault lookup for '{name}' failed ({category}: {detail}); falling back to environment/.env.")
            _record_keyvault_error(vault_url, category, detail)
    value = os.environ.get(name)
    if value is not None:
        return value, "environment"
    return default, None


def get_keyvault_status() -> dict:
    """Real status for the Azure Readiness / Secrets Management pages --
    never just 'configured': checks the vault's own connectivity with one
    cheap, free call (list_properties_of_secrets, first page only, never
    reads a secret VALUE), classified with the same error vocabulary
    azure_integrations.py uses everywhere else. Returns
    {"configured": bool, "outcome": "healthy"|"unhealthy"|"unknown",
    "error_category": str|None, "detail": str}."""
    import time as _time

    vault_url = os.environ.get("AZURE_KEY_VAULT_URL")
    if not vault_url:
        return {"configured": False, "outcome": "unknown", "error_category": None, "detail": "AZURE_KEY_VAULT_URL is not set."}

    start = _time.monotonic()
    try:
        client = _keyvault_client(vault_url)
        page = client.list_properties_of_secrets(max_page_size=1)
        next(iter(page), None)  # one network round-trip, never reads a secret value
        return {
            "configured": True, "outcome": "healthy", "error_category": None,
            "detail": "Live Key Vault connectivity check succeeded.",
            "duration_ms": round((_time.monotonic() - start) * 1000, 1),
        }
    except ImportError as e:
        return {"configured": True, "outcome": "unhealthy", "error_category": "dependency_missing", "detail": str(e)}
    except Exception as e:
        category = _categorize_keyvault_error(e)
        # A connectivity check has no specific secret to "not find" --
        # any real exception here is a genuine problem, never downgraded.
        return {"configured": True, "outcome": "unhealthy", "error_category": category, "detail": str(e)}


def set_env_secret(name: str, value: str) -> None:
    """Writes `name=value` into the real .env file at the repo root
    (creating it if it doesn't exist yet) and updates this process's
    os.environ immediately, so the very next get_secret() call --
    including from a different module that already imported this one --
    sees the new value without a restart. This is the Secrets Management
    admin page's write path: it only ever writes to .env, never to a
    Cosmos document or any other "ordinary configuration record" (the
    rule every config console this repo has built stays this strict
    about) -- .env is already this repo's own canonical secret store
    (secrets_loader.get_secret()'s own fallback), so writing here is
    the same trust boundary as editing it by hand, not a new one.

    Never call this with a value you don't want written to disk in
    plaintext -- .env is not encrypted. For anything beyond local/dev
    use, provision Key Vault instead (AZURE_KEY_VAULT_URL) and set the
    secret there directly; this function only ever touches .env."""
    if not name or not isinstance(name, str):
        raise ValueError("name must be a non-empty string.")
    _ENV_PATH.touch(exist_ok=True)
    set_key(str(_ENV_PATH), name, value)
    os.environ[name] = value


@lru_cache(maxsize=4)
def _keyvault_client(vault_url: str):
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient

    return SecretClient(vault_url=vault_url, credential=DefaultAzureCredential())


def _get_from_keyvault(vault_url: str, name: str):
    # Key Vault secret names can't contain underscores (only alphanumerics
    # and hyphens) — map OPENAI_API_KEY -> openai-api-key, matching the
    # naming convention in docs/azure-provisioning/phase2-cosmos-storage-keyvault.md.
    kv_name = name.lower().replace("_", "-")
    client = _keyvault_client(vault_url)
    secret = client.get_secret(kv_name)
    return secret.value
