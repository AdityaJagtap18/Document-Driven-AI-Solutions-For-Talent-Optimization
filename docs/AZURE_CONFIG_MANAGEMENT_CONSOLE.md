# Azure Configuration Management Console (Phase 1)

Extends the Azure Service Hub v2 (`docs/AZURE_SERVICE_HUB_V2.md`) with
configuration lifecycle, change history/rollback, explicit "planned"
adapters, and AI Observability correlation. This is an extension of the
existing console at `/admin/azure-services` — no new route, no duplicate
auth/config/telemetry system.

## What this pass added

1. **Two "planned" adapters**, verified against the repository (not
   assumed): `azure_ai_foundry` and `azure_ai_search`. Both have
   `status: "planned"`, empty `config_schema`, and all capabilities
   `False` — there is no code path in this repo for either yet (confirmed
   via `semantic-library/embeddings.py` and `vector_store.py`'s own
   docstrings: plain OpenAI API, local JSON+numpy store). The console
   shows them as distinct, non-interactive cards labeled "planned — not
   implemented," and the registry/validate/check/register functions all
   refuse to treat them as real. `ensure_default_integration_instances()`
   and its mock mirror were fixed to **never** auto-seed an "already in
   use" instance for a planned adapter — a real bug this pass caught
   (the original loop iterated every adapter unconditionally).
2. **Configuration change history**, distinct from health-check history.
   New `integration_config_history` container (`repositories.py`:
   `record_integration_config_change()` / `list_integration_config_history()`),
   recording every register / config_updated / activated / deactivated /
   rolled_back / registration_removed event with actor, timestamp, and a
   safe (secret-free) summary — never the resolved secret itself, since
   the register/update endpoints already reject secret fields in that
   payload. New endpoint: `GET .../config-history`.
3. **Revalidation on config change**: updating an integration's config (or
   rolling one back) now resets `last_connection_outcome`/
   `last_health_checked_at`/`last_failure` to null and drops an enabled
   integration back to disabled/`draft` — the spec's "revalidate restored
   configuration before activation," enforced structurally via the
   existing enable-requires-a-healthy-check gate, not left to convention.
4. **Rollback**: `POST .../config-history/{history_id}/rollback` restores a
   prior config snapshot as a **new** `config_version` — history is never
   mutated or deleted, and the restored config is not auto-enabled (same
   revalidation rule).
5. **`last_failure`** — a redacted summary of the most recent non-healthy
   check, cached on the instance for display without re-querying
   diagnostics history.
6. **AI Observability correlation**: connection checks and resource
   discovery attempts now emit a span via `observability.trace_stage()`
   (workflow `"azure_config"`), reusing the existing trace/span telemetry
   rather than building a second logging system. Metadata captures
   integration_id, adapter_type, config_version, outcome/error_category
   (checks) or supported/resource_count (discovery) — never secret values,
   candidate data, or prompt content.
7. **Frontend**: inventory list now shows planned adapters as separate
   cards; the Add Integration picker disables planned options with an
   explanatory label; detail view gained a **Configuration History** tab
   with a "Roll back to this" action (confirmation dialog, since this is
   a high-impact change); Overview tab shows a persistent `last_failure`
   banner, not just the result of the most recent action in this session.

## Capabilities table (updated)

| Service | Status | Live health check | Resource discovery | Resource write ops |
|---|---|---|---|---|
| Cosmos DB | ✅ supported | ✅ real | ✅ real (ARM) | ❌ none |
| Blob Storage | ✅ supported | ✅ real | ✅ real (ARM) | ❌ none |
| Document Intelligence | ✅ supported | ⚠️ config-presence only | ❌ unsupported | ❌ none |
| Azure Speech | ✅ supported | ⚠️ config-presence only | ❌ unsupported | ❌ none |
| Key Vault | ✅ supported | ⚠️ config-presence only | ❌ unsupported | ❌ none |
| Application Insights | ✅ supported | ⚠️ config-presence only | ❌ unsupported | ❌ none |
| **Azure AI Foundry** | ❌ **planned** | n/a | n/a | n/a |
| **Azure AI Search** | ❌ **planned** | n/a | n/a | n/a |

## RBAC

No new permissions were needed — `azure.config.write` (already existed for
the Configuration tab) now also gates config-history-based rollback;
`azure.config.read` gates both health-history and config-history reads,
consistent with the existing split. All 9 permissions from v2 are
unchanged.

## Tests

13 new tests added to `tests/test_ai_observability_azure_hub.py`:
`TestPlannedAdapters` (5 — registry metadata, validate/check honesty,
the default-seed fix, registration refusal) and
`TestConfigHistoryAndRollback` (8 — revalidation-on-update, history
listing, rollback success/404/wrong-entry-type, RBAC). Full suite:
**393 passed** (up from 380).

## What this pass deliberately did NOT build (stated, not hidden)

Mapped against the full spec's 18 sections — most of what it asks for
either already existed from Azure Service Hub v2 or is covered by this
pass's extensions. What's genuinely still missing:

- **Dedicated per-service pages** (`/admin/azure-services` has one
  unified Overview/Configuration/Resources/Diagnostics/History detail
  view per instance, not ten separate top-level pages for Key Vault,
  Cosmos DB, AI Foundry, etc. as the spec's section 2 lists). The same
  information is reachable, just organized by instance rather than by
  service category. Splitting into ten pages would substantially
  duplicate this console's existing tab structure for no functional
  gain — flagged here rather than built as a hollow restructuring.
- **Typed, per-service Pydantic-style config schemas.** `config_schema`
  is a list of `{key, label, required, secret}` dicts, not a distinct
  Python class per adapter. This is honest and sufficient for the six
  real adapters' actual fields, but isn't the "typed request/response
  schema" architecture section 14 describes.
- **A true Draft → Validate → Review → Activate lifecycle with a
  separate "Review" step requiring a second approver.** Current
  lifecycle is Draft → (validate via a separate call) → Active/Disabled,
  with no second-approver review gate. Enabling still requires a prior
  successful check, but there's no distinct "pending review" state.
- **Multi-environment secret references** (same integration, different
  secret per environment). `secrets_loader.get_secret()` reads one
  process-wide `.env`/Key Vault, with no per-environment secret
  namespace — `environment` on an `IntegrationInstance` is informational
  only, not a real secret-resolution scope.
- **Destructive-operation safeguards beyond "remove registration."**
  No adapter has any resource create/update/delete operation implemented
  (unchanged from v2), so the spec's elevated-permission/exact-scope/
  confirmation requirements for destructive Azure operations have no
  real operation to attach to yet. Removing a CDPI registration already
  requires a confirmation dialog and `azure.register`, and is
  audit-logged and config-history-logged.
- **Azure Resource Inventory as a cross-service dashboard.** Resource
  discovery works per-instance (Resources tab); there's no single page
  aggregating discovered resources across every instance.

## What requires real Azure credentials/resources to validate

Same as v2, unchanged: resource discovery's success path (only the
failure path — `ClientAuthenticationError`/missing SDK — is exercised in
this sandbox); any live health check beyond the config-presence case;
anything involving `azure-mgmt-resource` against a real subscription.
