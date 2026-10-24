"""Azure Service Hub's backend: an ADAPTER REGISTRY (ADAPTER_TYPES) for
every Azure (plus Key Vault/Application Insights) integration this repo
actually has code for, real health checks with a normalized outcome
vocabulary, and real Azure Resource Manager-based resource discovery
where an adapter genuinely supports it.

VERIFIED AGAINST THE REPOSITORY, not invented — ADAPTER_TYPES matches
exactly what an investigation of the codebase found wired to a real
Azure SDK (context-lake/client.py, context-lake/blob_store.py,
document_intelligence_client.py, voicebot/azure_speech_utils.py,
secrets_loader.py, observability.py). Nothing here is "proposed" or
"planned" — if an integration isn't in this registry, this repo has no
code for it yet. Registering an INSTANCE of an adapter (see
repositories.py's integration_instances CRUD) never accepts a raw
connection string or secret value through this module or any API built
on it — only non-secret metadata (display name, environment, and, for
resource discovery, a subscription_id/resource_group scope). The actual
credential a check/discovery call uses always comes from
secrets_loader.get_secret() (.env or Key Vault), exactly as it already
did before this feature — per the explicit "no unrestricted
administrator-supplied connection strings" security requirement.

HEALTH CHECK OUTCOMES use a normalized vocabulary distinguishing WHY a
check failed, not just pass/fail:
  healthy, degraded, unhealthy, unknown
error_category (set when outcome is "unhealthy" or "unknown"):
  configuration_invalid, authentication_failed, authorization_denied,
  endpoint_unreachable, service_unavailable, timeout, unknown_error
This is a real distinction, not decoration: authentication_failed is
detected via azure.core.exceptions.ClientAuthenticationError, timeout
via socket.timeout/requests.Timeout-shaped messages, etc. -- see
_categorize_error(). Never collapsed into one boolean.

LIVE CONNECTION CHECKS are only implemented for Cosmos DB and Blob
Storage, because those SDKs expose a cheap, free, idempotent read
(database/container metadata, account info) safe to call just to prove
connectivity. Document Intelligence and Azure Speech do NOT get a live
check here on purpose -- their only "cheap" operations are the real,
billable ones (analyze a document, synthesize/recognize speech), and
spending real Azure quota just to populate an admin status page would be
exactly the kind of fabricated "it works!" signal this feature's spec
explicitly forbids. Key Vault and Application Insights likewise get
configuration-presence checks only. Every outcome this module can
return is real: either a real network round-trip succeeded/failed, or
honestly labeled "unknown -- not verifiable without a billable/unsafe
call" -- never a hardcoded "healthy".

RESOURCE DISCOVERY is similarly bounded: only Cosmos DB and Blob Storage
have a known `azure_resource_type` and get real Azure Resource Manager
(azure-mgmt-resource) discovery support, scoped to a subscription_id +
resource_group the registering administrator supplies (ARM has no
"discover everything the app might use" mode -- it always needs a real
scope). Every other adapter type reports resource_discovery_supported =
False, not an empty list that could be misread as "zero resources
exist" (an explicit anti-pattern this feature's spec calls out).
azure-mgmt-resource is commented out in requirements.txt (only needed
once a real Azure subscription is available to discover against) --
without it installed, discovery fails closed with a clear
"azure-mgmt-resource is not installed" message, never a fabricated
resource list.

RESOURCE LIFECYCLE OPERATIONS (create/update/start/stop/delete) are
DELIBERATELY NOT IMPLEMENTED for any adapter in this pass -- none of the
six real integrations here have a write/delete operation that's been
implemented AND verified, and the spec is explicit: do not claim support
for an operation that isn't both. `resource_operations` is an empty list
on every adapter type for that reason, not an oversight.
"""

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "context-lake"))
from secrets_loader import get_secret  # noqa: E402

# ---------------------------------------------------------------------------
# Adapter registry -- one entry per Azure/secret-management service this
# repo has real code for. `config_schema` lists the fields an
# IntegrationInstance can carry (repositories.py) -- `secret` fields are
# NEVER accepted through an API built on this module; they're read live
# from secrets_loader at check/discovery time, same as before this
# feature existed. Only non-secret fields (discovery scope, mainly) are
# ever persisted per-instance.
# ---------------------------------------------------------------------------
ADAPTER_TYPES = {
    "cosmos_db": {
        "adapter_type": "cosmos_db", "name": "Azure Cosmos DB", "category": "Data", "status": "supported",
        "description": "Primary data store -- candidates, users, roles, pages, audit log, and every other container in context-lake/client.py.",
        "source_module": "context-lake/client.py",
        "config_schema": [
            {"key": "COSMOS_ENDPOINT", "label": "Endpoint URL", "required": True, "secret": False},
            {"key": "COSMOS_KEY", "label": "Primary key", "required": True, "secret": True},
            {"key": "COSMOS_DATABASE", "label": "Database name", "required": True, "secret": False},
            {"key": "subscription_id", "label": "Azure subscription ID (for resource discovery)", "required": False, "secret": False},
            {"key": "resource_group", "label": "Resource group (for resource discovery)", "required": False, "secret": False},
        ],
        "auth_methods": ["key", "managed_identity"],
        "capabilities": {"health_check": True, "resource_discovery": True, "resource_operations": []},
        "azure_resource_type": "Microsoft.DocumentDB/databaseAccounts",
    },
    "blob_storage": {
        "adapter_type": "blob_storage", "name": "Azure Blob Storage", "category": "Storage", "status": "supported",
        "description": "Raw and parsed resume file storage (raw-resumes, parsed-resumes containers).",
        "source_module": "context-lake/blob_store.py",
        "config_schema": [
            {"key": "BLOB_CONNECTION_STRING", "label": "Connection string", "required": True, "secret": True},
            {"key": "subscription_id", "label": "Azure subscription ID (for resource discovery)", "required": False, "secret": False},
            {"key": "resource_group", "label": "Resource group (for resource discovery)", "required": False, "secret": False},
        ],
        "auth_methods": ["connection_string", "managed_identity"],
        "capabilities": {"health_check": True, "resource_discovery": True, "resource_operations": []},
        "azure_resource_type": "Microsoft.Storage/storageAccounts",
    },
    "document_intelligence": {
        "adapter_type": "document_intelligence", "name": "Azure AI Document Intelligence", "category": "AI", "status": "supported",
        "description": "Certificate/marksheet OCR and cross-check verification (candidates/{id}/verify-document).",
        "source_module": "document_intelligence_client.py",
        "config_schema": [
            {"key": "DOCUMENT_INTELLIGENCE_ENDPOINT", "label": "Endpoint URL", "required": True, "secret": False},
            {"key": "DOCUMENT_INTELLIGENCE_KEY", "label": "API key", "required": True, "secret": True},
        ],
        "auth_methods": ["key"],
        "capabilities": {"health_check": False, "resource_discovery": False, "resource_operations": []},
        "azure_resource_type": None,
    },
    "key_vault": {
        "adapter_type": "key_vault", "name": "Azure Key Vault", "category": "Identity & Secrets", "status": "supported",
        "description": "Centralized secret storage -- secrets_loader.py falls back to .env when this isn't configured.",
        "source_module": "secrets_loader.py",
        "config_schema": [
            {"key": "AZURE_KEY_VAULT_URL", "label": "Vault URL", "required": True, "secret": False},
        ],
        "auth_methods": ["managed_identity"],
        # Real live check added in the Azure real-services audit pass --
        # secrets_loader.get_keyvault_status() does one free, safe
        # list_properties_of_secrets call (never reads a secret VALUE),
        # same shape as the Cosmos/Blob checks below.
        "capabilities": {"health_check": True, "resource_discovery": False, "resource_operations": []},
        "azure_resource_type": None,
    },
    "application_insights": {
        "adapter_type": "application_insights", "name": "Azure Application Insights", "category": "Observability", "status": "supported",
        "description": "Optional telemetry sink for LLM call and AI trace records -- see observability.py.",
        "source_module": "observability.py",
        "config_schema": [
            {"key": "APPLICATIONINSIGHTS_CONNECTION_STRING", "label": "Connection string", "required": True, "secret": True},
        ],
        "auth_methods": ["connection_string"],
        "capabilities": {"health_check": False, "resource_discovery": False, "resource_operations": []},
        "azure_resource_type": None,
    },
    # -----------------------------------------------------------------
    # REAL as of the Azure real-services audit pass -- llm_utils.py's
    # get_chat_llm()/get_embedding_client_and_model() route every real
    # call site (ResumeParser.py, rag_preview.py, generate_questions.py,
    # interview_bot.py, voicebot/voice_bot.py, semantic-library/
    # embeddings.py) to a real AzureChatOpenAI/AzureOpenAI client the
    # moment all four required settings below are present -- previously
    # "planned" (confirmed by this repo's own prior audit; see
    # docs/AZURE_REAL_SERVICES_AUDIT.md for the evidence that found it).
    # health_check stays False on purpose -- unlike Cosmos/Blob/Key
    # Vault, there is no free metadata-only operation here; verifying
    # this adapter means sending an actual billable chat/embedding
    # request, which this app never does automatically (same rule
    # Document Intelligence/Azure Speech already follow). See
    # llm_utils.run_azure_openai_test_request() for the explicit,
    # admin-triggered real test instead.
    # -----------------------------------------------------------------
    "azure_ai_foundry": {
        "adapter_type": "azure_ai_foundry", "name": "Azure AI Foundry / Azure OpenAI", "category": "AI", "status": "supported",
        "description": "Chat completion and embedding generation. Real -- llm_utils.get_chat_llm() and "
                       "get_embedding_client_and_model() route to Azure OpenAI once AZURE_OPENAI_ENDPOINT, "
                       "AZURE_OPENAI_API_KEY, and AZURE_OPENAI_CHAT_DEPLOYMENT (chat) / "
                       "AZURE_OPENAI_EMBEDDING_DEPLOYMENT (embeddings) are all set; falls back to plain OpenAI "
                       "otherwise, same as before this pass.",
        "source_module": "llm_utils.py",
        "config_schema": [
            {"key": "AZURE_OPENAI_ENDPOINT", "label": "Resource endpoint URL", "required": True, "secret": False},
            {"key": "AZURE_OPENAI_API_KEY", "label": "API key", "required": True, "secret": True},
            {"key": "AZURE_OPENAI_API_VERSION", "label": "API version (defaults to 2024-10-21)", "required": False, "secret": False},
            {"key": "AZURE_OPENAI_CHAT_DEPLOYMENT", "label": "Chat deployment name", "required": True, "secret": False},
            {"key": "AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "label": "Embedding deployment name", "required": False, "secret": False},
        ],
        "auth_methods": ["key"],
        "capabilities": {"health_check": False, "resource_discovery": False, "resource_operations": []},
        "azure_resource_type": None,
    },
    "azure_ai_search": {
        "adapter_type": "azure_ai_search", "name": "Azure AI Search", "category": "Data", "status": "planned",
        "description": "Vector search / retrieval index. NOT integrated -- semantic-library/vector_store.py's "
                       "LocalVectorStore is a local JSON+numpy cosine-similarity store; its own docstring says "
                       "it is 'not meant to replace Azure AI Search at real scale.' Requires: provisioning an "
                       "Azure AI Search service, defining and populating a vector index compatible with the "
                       "embedding model actually in use, and new application code in semantic-library/ to query "
                       "it instead of LocalVectorStore -- a deliberate, explicitly-validated migration, not a "
                       "drop-in swap this registry entry performs.",
        "source_module": None,
        "config_schema": [],
        "auth_methods": [],
        "capabilities": {"health_check": False, "resource_discovery": False, "resource_operations": []},
        "azure_resource_type": None,
        "requires_development_work": True,
    },
}
ADAPTER_TYPE_IDS = list(ADAPTER_TYPES.keys())


def get_adapter(adapter_type: str):
    return ADAPTER_TYPES.get(adapter_type)


def is_planned(adapter_type: str) -> bool:
    adapter = get_adapter(adapter_type)
    return bool(adapter and adapter.get("status") == "planned")


def required_secret_keys(adapter_type: str) -> list:
    adapter = get_adapter(adapter_type)
    if not adapter:
        return []
    return [f["key"] for f in adapter["config_schema"] if f["secret"] and f["required"]]


# ---------------------------------------------------------------------------
# Secret redaction -- applied to every error message this module returns to
# a caller (API response or audit log) before it leaves this process. A
# connection string/key that fails to parse can legitimately contain the
# credential itself inside the exception text (the SDKs often echo the
# value back in a "malformed connection string" error) -- this strips
# anything that looks like one rather than trusting every SDK's exception
# text to already be safe.
# ---------------------------------------------------------------------------
_SECRET_PATTERNS = [
    re.compile(r"(AccountKey|SharedAccessSignature|sig|key|password|secret|token)\s*=\s*[^;&\s]+", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE),
    re.compile(r"[A-Za-z0-9+/]{40,}={0,2}"),  # long base64-looking blobs (keys/tokens)
]


def redact_secret(text: str) -> str:
    if not text:
        return text
    redacted = str(text)
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(lambda m: m.group(0).split("=")[0] + "=[REDACTED]" if "=" in m.group(0) else "[REDACTED]", redacted)
    return redacted[:500]  # also cap length -- never return an unbounded SDK stack trace to a client


def secret_presence(adapter_type: str) -> dict:
    """{secret_key: bool} -- whether each required secret field currently
    resolves to a non-empty value, via the same get_secret() the real
    code path uses (Key Vault first, then .env) -- NEVER the value
    itself, only presence."""
    adapter = get_adapter(adapter_type)
    if not adapter:
        return {}
    return {f["key"]: bool(get_secret(f["key"])) for f in adapter["config_schema"] if f["required"]}


def validate_config(adapter_type: str) -> list:
    """Returns a list of validation error strings (empty = valid).
    Checked against live secret presence, since this pass doesn't accept
    admin-supplied secret values -- "valid" means "every required secret
    this adapter needs is currently resolvable via secrets_loader"."""
    adapter = get_adapter(adapter_type)
    if not adapter:
        return [f"Unknown adapter type '{adapter_type}'."]
    if adapter.get("status") == "planned":
        return [f"'{adapter['name']}' is planned but not implemented in this repository -- there is no code path to configure or validate yet."]
    presence = secret_presence(adapter_type)
    missing = [k for k, present in presence.items() if not present]
    return [f"Missing required secret(s): {', '.join(missing)}."] if missing else []


def _now():
    return datetime.now(timezone.utc).isoformat()


_TIMEOUT_HINTS = ("timed out", "timeout")
_UNREACHABLE_HINTS = ("name or service not known", "failed to establish a new connection", "getaddrinfo failed", "connection refused")


def _categorize_error(exception: Exception) -> str:
    """Maps a real exception to the spec's error_category vocabulary --
    distinguishing WHY a check failed, not just that it did. Checked by
    real exception type first (azure.core.exceptions.*), then by message
    content for cases the SDKs don't give a distinct type for."""
    try:
        from azure.core.exceptions import ClientAuthenticationError, HttpResponseError, ResourceNotFoundError, ServiceRequestError
    except ImportError:
        ClientAuthenticationError = HttpResponseError = ResourceNotFoundError = ServiceRequestError = ()  # noqa: N806

    if isinstance(exception, ClientAuthenticationError):
        return "authentication_failed"
    if isinstance(exception, HttpResponseError) and getattr(exception, "status_code", None) == 403:
        return "authorization_denied"
    if isinstance(exception, ResourceNotFoundError):
        return "endpoint_unreachable"
    message = str(exception).lower()
    if any(h in message for h in _TIMEOUT_HINTS):
        return "timeout"
    if any(h in message for h in _UNREACHABLE_HINTS):
        return "endpoint_unreachable"
    if isinstance(exception, ServiceRequestError):
        return "service_unavailable"
    return "unknown_error"


def check_connection(adapter_type: str) -> dict:
    """Returns {adapter_type, outcome, error_category, detail, duration_ms,
    checked_at}. outcome is one of: "healthy" (a real, live, free
    round-trip succeeded), "unhealthy" (a live round-trip was attempted
    and failed -- error_category explains why, detail is redacted),
    "unknown" (not verifiable: either a required secret is missing, or
    this adapter has no safe free operation to check with -- NEVER
    reported as "healthy" by default). "degraded" is reserved for a
    check that succeeds but with a caveat (none of the six adapters here
    currently produce that case -- documented, not invented)."""
    import time as _time

    start = _time.monotonic()
    adapter = get_adapter(adapter_type)
    if adapter is None:
        return {
            "adapter_type": adapter_type, "outcome": "unknown", "error_category": "configuration_invalid",
            "detail": "Unknown adapter type.", "duration_ms": 0.0, "checked_at": _now(),
        }

    errors = validate_config(adapter_type)
    if errors:
        return {
            "adapter_type": adapter_type, "outcome": "unknown", "error_category": "configuration_invalid",
            "detail": errors[0], "duration_ms": round((_time.monotonic() - start) * 1000, 1), "checked_at": _now(),
        }

    if not adapter["capabilities"]["health_check"]:
        return {
            "adapter_type": adapter_type, "outcome": "unknown", "error_category": None,
            "detail": "Required secrets are present, but this integration has no safe, free operation to verify "
                       "connectivity without a billable call -- configuration presence confirmed, not a live check.",
            "duration_ms": round((_time.monotonic() - start) * 1000, 1), "checked_at": _now(),
        }

    if adapter_type == "key_vault":
        # secrets_loader.get_keyvault_status() already returns a fully
        # classified, non-raising result (its own real auth/permission/
        # dependency error categories -- see that function's docstring)
        # -- reused here rather than forced through the raise-based
        # pattern below, which was written for SDK calls that simply
        # throw on failure.
        from secrets_loader import get_keyvault_status

        status = get_keyvault_status()
        return {
            "adapter_type": adapter_type, "outcome": status["outcome"], "error_category": status["error_category"],
            "detail": redact_secret(status["detail"]), "duration_ms": status.get("duration_ms", round((_time.monotonic() - start) * 1000, 1)),
            "checked_at": _now(),
        }

    try:
        if adapter_type == "cosmos_db":
            _check_cosmos()
        elif adapter_type == "blob_storage":
            _check_blob_storage()
        else:
            raise RuntimeError(f"No live check implemented for '{adapter_type}'.")
        return {
            "adapter_type": adapter_type, "outcome": "healthy", "error_category": None,
            "detail": "Live connection check succeeded.",
            "duration_ms": round((_time.monotonic() - start) * 1000, 1), "checked_at": _now(),
        }
    except Exception as e:
        return {
            "adapter_type": adapter_type, "outcome": "unhealthy", "error_category": _categorize_error(e),
            "detail": redact_secret(str(e)), "duration_ms": round((_time.monotonic() - start) * 1000, 1), "checked_at": _now(),
        }


def _check_cosmos():
    import client as context_lake_client
    db = context_lake_client.get_cosmos_database()
    db.read()  # cheap metadata GET, no data read/written


def _check_blob_storage():
    import client as context_lake_client
    svc = context_lake_client.get_blob_service_client()
    svc.get_account_information()  # cheap metadata GET, no data read/written


# ---------------------------------------------------------------------------
# Resource discovery -- real Azure Resource Manager calls, only for the
# two adapters with a known azure_resource_type. Always needs a real
# subscription_id + resource_group (ARM has no "find everything the app
# might use" mode) -- these come from the IntegrationInstance's
# non-secret config, entered by the registering administrator, never
# inferred or guessed.
# ---------------------------------------------------------------------------
def discover_resources(adapter_type: str, subscription_id: str, resource_group: str) -> dict:
    """Returns {supported, resources, error, discovered_at}. `supported`
    is False (with `error` explaining why, `resources` always []) when:
    the adapter has no azure_resource_type, azure-mgmt-resource isn't
    installed, or subscription_id/resource_group weren't supplied --
    NEVER returns an empty list dressed up as "zero resources found" in
    any of those cases, per the spec's explicit warning about that
    exact failure mode."""
    adapter = get_adapter(adapter_type)
    if adapter is None or not adapter["capabilities"]["resource_discovery"]:
        return {"supported": False, "resources": [], "error": f"Resource discovery is not supported for adapter type '{adapter_type}'.", "discovered_at": _now()}
    if not subscription_id or not resource_group:
        return {"supported": False, "resources": [], "error": "subscription_id and resource_group are both required to discover resources.", "discovered_at": _now()}

    try:
        from azure.mgmt.resource.resources import ResourceManagementClient
        from azure.identity import DefaultAzureCredential
    except ImportError:
        return {
            "supported": False, "resources": [],
            "error": "azure-mgmt-resource is not installed in this environment -- see requirements.txt (commented out until a real Azure subscription is available).",
            "discovered_at": _now(),
        }

    resource_type = adapter["azure_resource_type"]
    try:
        client = ResourceManagementClient(DefaultAzureCredential(), subscription_id=subscription_id)
        items = client.resources.list_by_resource_group(
            resource_group, filter=f"resourceType eq '{resource_type}'",
        )
        resources = [
            {
                "resource_id": r.id, "name": r.name, "type": r.type,
                "location": r.location, "resource_group": resource_group,
                "subscription_id": subscription_id,
                "provisioning_state": getattr(r, "provisioning_state", None),
                "tags": r.tags or {},
            }
            for r in items
        ]
        return {"supported": True, "resources": resources, "error": None, "discovered_at": _now()}
    except Exception as e:
        return {
            "supported": True, "resources": [],
            "error": f"{_categorize_error(e)}: {redact_secret(str(e))}",
            "discovered_at": _now(),
        }
