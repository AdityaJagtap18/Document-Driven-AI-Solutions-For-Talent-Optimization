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

from dotenv import load_dotenv

load_dotenv()


def get_secret(name: str, default=None):
    """Looks up `name` in Key Vault first (if AZURE_KEY_VAULT_URL is set in
    the environment), then falls back to os.environ. Any Key Vault failure
    (not provisioned, no access, network error, secret doesn't exist there)
    is caught and logged as a warning — it never raises, it just falls
    through to the environment, so a misconfigured Key Vault degrades to
    today's .env-only behavior instead of breaking the script."""
    vault_url = os.environ.get("AZURE_KEY_VAULT_URL")
    if vault_url:
        try:
            value = _get_from_keyvault(vault_url, name)
            if value is not None:
                return value
        except Exception as e:
            print(f"[WARN] Key Vault lookup for '{name}' failed ({e}); falling back to environment/.env.")
    return os.environ.get(name, default)


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
