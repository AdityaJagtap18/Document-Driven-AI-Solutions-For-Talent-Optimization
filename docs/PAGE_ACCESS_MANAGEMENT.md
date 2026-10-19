# Dynamic Page Access Management

Real, backend-enforced, per-role navigation control over every page this
app has — layered on top of the existing RBAC system (`auth.py`,
`docs/IAM_RBAC.md`) rather than a second, parallel authorization
mechanism.

## Architecture inspection (what's actually in this repo)

- **No client-side router.** No React Router, no bundler, no `/path`
  URLs per screen (`frontend/App.jsx`'s own doc comment says so). Every
  "page" is a tab key switched entirely in React state
  (`frontend/App.jsx`'s `NAV_ITEMS`/`activeTab`). There is nothing
  resembling `/candidates/:candidateId` — the one candidate-detail view
  that exists (`StageTimelineModal`) is a modal opened from JS state, not
  a navigable route.
- **Existing RBAC**: `auth.py` (permission catalog, role catalog, JWT),
  `azure-functions/api_blueprint.py`'s `require_permission()` (the real
  authorization gate on every protected endpoint), `context-lake/
  repositories.py` (Cosmos CRUD), `frontend/App.jsx` (nav), documented
  fully in `docs/IAM_RBAC.md`.
- **Existing mock/demo mode**: `frontend/src/mockApiClient.js`, a
  hand-maintained mirror of the real backend's behavior, used by default
  since no Azure Functions host is deployed in this environment.
- **Persistence**: Cosmos DB via `context-lake/client.py`'s
  `COSMOS_CONTAINERS` + `repositories.py`.

Given no router exists, the feature request's suggested discovery
mechanisms (route metadata, a build-generated route manifest) don't
apply literally. The adaptation used: a single code-defined catalog
(`auth.PAGES`) **is** this app's route registry — the same pattern this
app's RBAC work already established for `auth.PERMISSIONS`/
`auth.DEFAULT_ROLES`. This is documented in `auth.PAGES`'s own docstring,
not hidden.

## Actual pages discovered in the repository

Every tab `frontend/App.jsx`'s old `NAV_ITEMS` already had, now expressed
as registry entries (`page_id` / `route` / module / permission):

| page_id | route (tab) | Module | Required permission |
|---|---|---|---|
| `dashboard` | dashboard | Candidate Management | `candidates.read` |
| `admin_reports` | reports | Analytics and Reports | `analytics.read` |
| `pipeline` | pipeline | Interview Management | `interviews.manage` |
| `review_queue` | review | Candidate Management | `candidates.update` |
| `recruiter_reports` | recruiter-reports | Analytics and Reports | `screening.propose` |
| `user_management` | users | User Management | `users.read` |
| `role_management` | roles | Role & Permission Management | `roles.read` |
| `page_access_management` | page-access | Page Access Management | `pages.manage` (new) |
| `candidate_portal` | portal | Resume Upload and Parsing | — (role-gated directly) |

No pages were invented. `route_type` is `"page"` for all nine — there are
no nested or parameterized routes in this app yet to classify otherwise.

## Page registry and permission model

- **`auth.PAGES`** (code catalog, `auth.py`): `page_id`, `name`, `route`,
  `module`, `description`, `required_permission`, `route_type`,
  `is_protected`. Static, developer-maintained — adding a page here
  alongside its `App.jsx` nav entry is this app's "route-registration
  mechanism."
- **Cosmos `pages` container** (`context-lake/client.py`): the persisted
  registry, reconciled against `auth.PAGES` by `repositories.sync_pages()`.
  Adds the *runtime* fields: `discovery_status` (`discovered` | `configured`
  | `stale`), `last_discovered_at`, `first_discovered_at`.
- **Cosmos `page_access_policies` container**: one document per
  `(page_id, role_key)` pair, id `"{page_id}:{role_key}"` (no duplicate-
  policy risk), `{allowed, created_by, updated_by, updated_at}`.
  **Deny-by-default**: a pair with no document at all is not granted —
  see `auth.effective_pages()`.
- **No new role catalogue.** Page policies reference the *existing* role
  catalog (`auth.DEFAULT_ROLES`/custom roles) directly, by `role_key`.
- **Page access is separate from, and does not replace, operation
  permissions.** `candidate_portal`'s `required_permission` is `null` —
  it's gated by the `candidate` role directly in its page policy, not an
  operation permission (mirrors the existing Candidate role design in
  `docs/IAM_RBAC.md`). Every other page's `required_permission` field is
  purely informational/reconciliation metadata shown in the config UI
  ("Underlying operation permission: X") — actual page-level authorization
  is 100% policy-driven (`effective_pages()`), and a page's own API calls
  independently re-check their own operation permission regardless of
  page access (e.g., reaching the Review Queue page does not grant
  `candidates.update` — that's still checked per-call, same as before this
  feature).
- **`pages.manage`**: one new permission, added to the existing catalog,
  module "Page Access Management." Only `admin` (the top-level role, see
  `docs/IAM_RBAC.md`'s no-Super-Admin decision) holds it by default.

## Discovery and lifecycle (`repositories.sync_pages()`)

- **First run** (empty `pages` container): registers every `auth.PAGES`
  entry and seeds `page_access_policies` from
  `auth.default_page_access_policies()` — this reproduces each role's
  *existing* implicit access (derived from whether that role already held
  the page's `required_permission`) as an explicit policy, so shipping
  this feature doesn't regress every page to "nobody can reach it" on day
  one.
- **A page added to `auth.PAGES` after that first run**: discovered with
  **zero** policies — deny-by-default, a clear warning (`discovery_status:
  "discovered"`) in the Admin Panel, and the config modal shows "This page
  was just discovered and has no access policy yet — every role is
  currently denied."
- **Rediscovery preserves policies**: `sync_pages()` only ever upserts a
  page's *metadata* fields, never `page_access_policies` — an admin's
  saved configuration survives every later sync.
- **Metadata changes** (renamed, moved module, etc.) are reported as
  `updated`, matched by the stable `page_id`, not by name/route string.
- **Removed pages are marked `stale`, never deleted** — policies survive
  in case the route reappears (e.g., a page temporarily missing from a
  dev build). A stale page that reappears is re-synced back to
  `configured` (if it still has policies) automatically.
- **Duplicate `page_id`s**: impossible by construction (`sync_pages()`
  upserts by `page_id` as the Cosmos document id); `tests/
  test_page_access.py::test_edge_no_duplicate_page_ids_in_the_catalog`
  guards the static catalog itself.
- Triggered automatically at Function App / `bootstrap_iam.py` startup
  (mirrors `ensure_default_roles()`), and manually via the Admin Panel's
  "Sync Pages" button (`POST /admin/pages/sync`) — e.g. right after a
  deploy that added a page, with no redeploy/restart needed for the sync
  itself.

## Admin routes and APIs added

- `GET /admin/pages` — list + filter (`module`, `discovery_status`,
  `is_protected`), each row includes `roles_granted`/`roles_granted_count`.
- `POST /admin/pages/sync` — runs discovery, audit-logs every new/
  updated/stale page_id.
- `GET /admin/pages/{page_id}/access` — page metadata + the full role ×
  allowed matrix (every role in the system, not just roles with an
  explicit policy — a role with none is correctly reported `allowed:
  false`, the real deny-by-default state).
- `PUT /admin/pages/{page_id}/access` — batch `{policies: [{role_key,
  allowed}, ...]}`; validates every role_key exists before writing
  anything (no half-applied update on a typo).
- `GET /auth/me` now also returns `pages: [...]` — the caller's effective
  page set, computed the same way as `permissions`.
- Frontend: `frontend/src/components/PageAccessManagement.jsx` (new Admin
  Panel tab, `/admin/page-access` tab key `page-access`), searchable/
  filterable table + a per-page config modal. `App.jsx`'s `NAV_ITEMS` now
  keys every tab by `page_id` and filters by the `pages` array from
  `GET /auth/me`, replacing the old raw-permission-based nav filter.

All five new/changed endpoints require `pages.manage`, same
`require_permission()` gate pattern as every other `admin/*` route — not
a separate authorization path.

## Persistence and synchronization strategy

Two new Cosmos containers (`pages`, `page_access_policies`), both
partition key `/id`, reusing the existing `get_container()`/CRUD
conventions (`context-lake/repositories.py`). No duplicate permission
data — `required_permission` on a page document is descriptive metadata
only; actual authorization is `effective_pages()` against
`page_access_policies`, never a second permission-evaluation path.

A page-access change takes effect on the affected user's **next
`GET /auth/me`** (next page load, or the existing session-revalidation
effect in `App.jsx`) — no cache to invalidate (this backend issues no
permission/page claims inside the JWT itself, see `docs/IAM_RBAC.md`;
every check re-reads current state), and no redeploy for an ordinary
policy change. Verified live: revoked Recruiter's access to the Pipeline
page as Admin, signed in as Recruiter immediately after, and the
Pipeline tab was already gone — no code change, no restart.

## Frontend and backend authorization enforcement

- **Frontend**: `App.jsx`'s single tab-switch chokepoint now gates on
  `pages` (not raw permissions) — unauthorized roles never see the nav
  link, and there's no URL to "manually enter" in this app (no router),
  so the closest equivalent attack (tampering with nav state in devtools
  to force an unlisted tab to render) still hits a page whose own data
  calls independently 401/403, verified below.
- **Backend**: `require_permission(req, "pages.manage")` on every
  `admin/pages*` endpoint, same pattern as every other protected route —
  not a separate/weaker mechanism. Verified live: calling
  `window.CDPI_MOCK_API.listPages()` directly from devtools while signed
  in as `recruiter` (who lacks `pages.manage`) returns a real 403, not
  data.
- Hiding a nav link is explicitly **not** treated as the security
  boundary anywhere in this implementation — every admin/pages* mutation
  and read is independently permission-checked server-side (or in the
  mock mirror, which enforces the identical rule set).

## Secure defaults for newly discovered pages

Verified in `tests/test_page_access.py::TestSyncPages::
test_golden_new_catalog_page_is_discovered_with_no_policies`: a page
added to the catalog after the first sync gets `discovery_status:
"discovered"` and **zero** `page_access_policies` documents — nobody can
reach it until an admin explicitly grants it, and the Admin Panel surfaces
this as "Needs configuration" plus an in-modal warning banner.

## Tests executed and results

`tests/test_page_access.py` (22 tests, all passing) covers:
- **Discovery**: first-run seeding reproduces today's implicit access;
  Candidate is only ever granted `candidate_portal`; rediscovery is
  idempotent and preserves an admin's explicit edit (including who/when);
  a new catalog page is discovered with zero policies; a removed page is
  marked stale, not deleted, policies intact; a stale page that reappears
  is re-synced to `configured`; metadata changes are reported as
  `updated`; no duplicate `page_id`s in the static catalog.
- **Authorization** (`effective_pages()` unit tests + endpoint tests):
  deny-by-default for an unconfigured page; grants only explicitly
  allowed pages; unions across multiple roles; `pages.manage`-gated
  endpoints correctly 401 (no token) / 403 (wrong role) / 200 (right
  role); `GET /auth/me` embeds the correct effective page set.
  Candidate/Recruiter restrictions were exercised directly in the
  existing IAM test suite (`docs/IAM_RBAC.md`) and remain unaffected —
  page access is additive, not a replacement.
- **Administration**: an unknown role in a policy batch is rejected
  (400) *before* anything is written (verified no partial write);
  non-bool `allowed` rejected; an unknown `page_id` returns 404; a
  non-`pages.manage` actor is refused (403) on every admin/pages*
  endpoint.

Full suite: **`python -m pytest tests/` → 303 passed** (all pre-existing
tests kept green; 22 new for this feature specifically, plus the shared
IAM test fixtures were moved from `tests/test_api_blueprint.py` into
`conftest.py` so the same fixture account applies repo-wide — pytest
autouse fixtures only apply within the module/conftest scope they're
defined in, not wherever that module is imported from, which the first
draft of `test_page_access.py` ran into directly).

Manually verified live in the browser (mock mode): Admin sees all 8
non-portal pages after first sync with roles matching today's actual
access; opened the Pipeline page's config modal, revoked Recruiter,
saved; signed out and back in as Recruiter — Pipeline tab was gone
immediately; a direct `listPages()` call as Recruiter returned a real
403. Zero console errors through the full flow (checked in a fresh
browser tab, since a long-lived tab's console history accumulates past
errors across reloads and can look misleading).

## Known limitations / things that remain honest gaps

- **No actual client-side router exists**, so "route guard," "direct URL
  navigation," and "nested/parameterized routes" are all adapted to this
  app's tab-based reality rather than literally implemented — documented
  above and in `auth.PAGES`'s docstring, not silently reinterpreted.
- **`mockApiClient.js`'s page catalog/policies are hand-duplicated** from
  `auth.py`/`repositories.py` (same documented convention already used
  for `DEFAULT_PIPELINE_STAGES`/roles/permissions — no bundler to share
  one source of truth between Python and a plain `<script>` file).
- **No per-row revert/undo UI** for a page-access change — an admin
  re-opens the config modal and flips it back; there's no change-history
  view beyond the audit log (`page_access_changed` entries record
  before-state only via the audit trail, not a one-click revert button).
- **`required_permission` is not currently enforced as a hard
  precondition for granting page access** (e.g. nothing stops an admin
  from granting the Pipeline page to a role that lacks
  `interviews.manage` — the page would render but its own data calls
  would 403). This is intentional per the spec ("Page access must
  complement, not replace, resource-level authorization" — the two are
  independent by design), but worth stating plainly: a misconfigured
  grant doesn't silently expose data, it just produces a page that loads
  and then shows permission errors on its own calls.
