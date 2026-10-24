# Azure Service Hub v2

Rebuild of the Azure Service Hub admin module (`/admin/azure-services`) from a
static, hardcoded integration list into a real **adapter registry +
registered instance** architecture, matching the pattern this app already
uses for Dynamic Page Access Management (`auth.PAGES` + `repositories.sync_pages()`)
and the AI Observability Center v2 (trace/span schema computed at read time).

## What changed and why

The v1 pass (`docs/AI_OBSERVABILITY_AZURE_HUB.md`) shipped one flat,
hardcoded list of six integrations with a single `status` field. That
collapsed genuinely different facts into one value and had no way to
register a *new* integration, edit its configuration, track health-check
*history* (only the latest result), or discover real Azure resources. This
rebuild replaces that with:

- **`azure_integrations.ADAPTER_TYPES`** — a code-defined registry of what
  this repo has real, verified code for (six adapters: Cosmos DB, Blob
  Storage, Document Intelligence, Azure Speech, Key Vault, Application
  Insights). Each entry declares its `config_schema` (which fields exist,
  which are secret), `capabilities` (`health_check`, `resource_discovery`,
  `resource_operations`), and `azure_resource_type` (an ARM resource type
  string, only for Cosmos DB and Blob Storage).
- **`repositories.py`'s `integration_instances` / `integration_health_checks`
  containers** — administrator-registered *instances* of an adapter type,
  each with its own non-secret config, enabled/disabled state, and a full
  append-only health-check history (not just a cached latest result).
- **A multi-dimensional status vocabulary** — `configured` / `enabled` /
  `last_connection_outcome` (`healthy` / `unhealthy` / `unknown` /
  `degraded`) / `error_category` are tracked and displayed as **separate
  badges**, never collapsed into one green/red indicator. An integration
  can be configured but disabled, or enabled with its last check unhealthy
  — these are different facts an administrator needs to see independently.

## Security rule (unchanged from v1, re-verified this pass)

No API or UI built on this module ever accepts a secret VALUE. Only
non-secret metadata (`display_name`, `environment`, and, for resource
discovery, `subscription_id`/`resource_group`) is ever persisted on an
`IntegrationInstance`. The actual credential always comes live from
`secrets_loader.get_secret()` (.env or Key Vault) at check/discovery time —
exactly as it did before this feature existed. Both the register and
config-update endpoints explicitly reject any `config` payload that
contains a field the adapter's `config_schema` marks `secret: true`.

## The three never-conflated actions

The spec was explicit that these must not be treated as equivalent:

1. **Remove registration** (`DELETE /admin/azure-services/{id}`) — deletes
   only the CDPI-side `IntegrationInstance` document.
2. **Disable** (`PUT /admin/azure-services/{id}/enabled`) — turns off CDPI's
   *use* of a still-registered integration.
3. **Delete the real Azure resource** — there is no code path anywhere in
   this module, or this repo, that can do this. `resource_operations` is an
   empty list on every adapter type, and this is stated as deliberate, not
   an oversight.

## Capabilities table (verified against the repository, not assumed)

| Adapter | Live health check | Resource discovery | Resource create/update/delete |
|---|---|---|---|
| Cosmos DB | ✅ real (cheap `database.read()`) | ✅ real (ARM, needs subscription+RG) | ❌ not implemented |
| Blob Storage | ✅ real (cheap `get_account_information()`) | ✅ real (ARM, needs subscription+RG) | ❌ not implemented |
| Document Intelligence | ⚠️ config-presence only (no safe free operation) | ❌ not supported (no `azure_resource_type`) | ❌ not implemented |
| Azure Speech | ⚠️ config-presence only | ❌ not supported | ❌ not implemented |
| Key Vault | ⚠️ config-presence only | ❌ not supported | ❌ not implemented |
| Application Insights | ⚠️ config-presence only | ❌ not supported | ❌ not implemented |

"Config-presence only" means: required secrets resolve via
`secrets_loader.get_secret()`, but this module makes **no live network
call** for that adapter, because its only cheap operations would be real,
billable ones (analyze a document, synthesize speech) — spending real Azure
quota just to populate an admin status page was explicitly out of scope.
This is reported as `outcome: "unknown"`, never `"healthy"`.

Resource discovery, even where `supported: true`, requires
`azure-mgmt-resource` (commented out in `requirements.txt` until a real
Azure subscription exists) **and** real Azure credentials via
`DefaultAzureCredential()`. In this sandbox neither a provisioned
subscription nor real credentials exist, so discovery always returns a
real `authentication_failed` (or `azure-mgmt-resource is not installed`)
error with `resources: []` — never a resource list, and never a bare empty
list without that explanation.

## RBAC (9 granular permissions, least-privilege, not one blanket gate)

| Permission | Grants |
|---|---|
| `azure.read` | View the integration inventory (cached status only) |
| `azure.config.read` | View one integration's full detail: config, resources, diagnostics |
| `azure.manage` | Run a live connection check |
| `azure.register` | Register a new integration, or remove a registration |
| `azure.config.write` | Edit an integration's non-secret configuration |
| `azure.enable` | Enable/disable CDPI's use of an integration |
| `azure.resources.discover` | Run real ARM resource discovery |
| `azure.resources.write` | Reserved — no adapter implements a verified write op |
| `azure.resources.delete` | Reserved — no adapter implements a verified delete op |

Only the `admin` role (the single top-level role, no separate Super Admin
tier) holds all nine by default.

## API endpoints (`azure-functions/api_blueprint.py`)

`GET /admin/azure-services/adapters`, `GET /admin/azure-services`,
`GET /admin/azure-services/{id}`, `POST /admin/azure-services/validate`,
`POST /admin/azure-services`, `PUT /admin/azure-services/{id}/config`,
`PUT /admin/azure-services/{id}/enabled`,
`POST /admin/azure-services/{id}/check`,
`GET /admin/azure-services/{id}/health-history`,
`POST /admin/azure-services/{id}/discover-resources`,
`DELETE /admin/azure-services/{id}`.

Config updates use optimistic concurrency (`expected_version` must match
the instance's current `config_version` or the call is refused with 409) so
a stale edit from a second admin tab can't silently overwrite a more recent
change. Enabling an integration is refused with 409 unless its last
recorded health check succeeded (`last_connection_outcome == "healthy"`).
Connection checks are rate-limited per (actor, integration) at 10 seconds,
same as v1.

## Tests

49 tests in `tests/test_ai_observability_azure_hub.py` cover the adapter
registry, `check_connection()`'s outcome/error-category vocabulary,
`discover_resources()`'s not-supported/missing-scope/real-failure paths,
and every new endpoint (registration incl. duplicate-id and secret-field
rejection, config update incl. stale-version 409, enable/disable incl. the
"needs a prior healthy check" safeguard, health history, resource
discovery, registration removal verified to never touch the real
resource, and RBAC on the stronger `azure.config.read` vs. `azure.read`
split). Full suite: 380 passed.

## Frontend

`AzureServiceHub.jsx` rebuilt as: an inventory list (multi-badge cards, not
one status dot) with an "Add Integration" wizard (adapter picker →
non-secret config → validate → register, disabled by default); and a
Service Details view with Overview / Configuration / Resources /
Diagnostics tabs. `apiClient.js`/`mockApiClient.js` carry the full new
method set; mock mode maintains its own in-memory `IntegrationInstance`
store (register/update/enable/check all persist for the session) and
always reports honest `unknown`/`not supported` outcomes, never a
fabricated "healthy" status or resource list. `App.jsx` now tracks the
signed-in user's effective `permissions` (from `GET /auth/me`) and passes
them down, so action buttons (Add Integration, Enable, Remove, Discover)
are hidden for a role that doesn't hold the matching permission — the
backend remains the only real enforcement point either way.

Verified live in-browser (fresh tab, `admin`/`Admin#12345`): inventory
renders six seeded instances with distinct badges; opened Cosmos DB's
detail view; ran a connection check (correctly returned
`unknown`/`configuration_invalid`); Resources tab correctly reported
"subscription_id and resource_group are both required"; Configuration and
Diagnostics tabs rendered correctly, diagnostics showing the just-run
check in history; registered a new `document_intelligence` instance via
the Add Integration modal, confirmed it appeared **disabled** in the list.
Confirmed the `recruiter` role sees no "Azure Service Hub" nav item at all
(deny-by-default page access, unchanged from the Page Access Management
feature). No console errors at any point.

## Known limitations (stated, not hidden)

- Resource discovery is only genuinely exercisable with a real Azure
  subscription, real credentials, and `azure-mgmt-resource` installed —
  none of which exist in this sandbox. The failure paths are real
  (verified against actual `ClientAuthenticationError`s), not simulated,
  but no success path has been observed end-to-end here.
- No resource create/update/delete operation exists for any adapter.
- Rate limiting on connection checks is in-memory per process — a real
  limitation under multi-instance Functions scale-out, same caveat as v1.
- `environment` is a free-text field, not a structured multi-environment
  model — one `IntegrationInstance` per environment an administrator wants
  tracked separately.
