# IAM-Style User and Role Management (RBAC)

Real, backend-enforced authentication and role-based authorization,
replacing the entirely client-side demo login that existed before this
feature (`Login.jsx` picking a hardcoded account from a dropdown-style
list, checked in pure JS, with zero backend involvement).

## What was found (investigation)

- No authentication existed anywhere in this repo. `Login.jsx` held a
  `DEMO_ACCOUNTS` object and compared a typed password against it client-side.
- `azure-functions/api_blueprint.py`'s `auth_level=func.AuthLevel.FUNCTION`
  gate is a single shared function key with no per-user or per-role
  distinction — anyone holding the one key could call any endpoint.
- `bcrypt` was not installed; `PyJWT` was already a dependency (used
  nowhere yet). Both verified working in this environment before writing
  any code.
- No `users`/`roles` persistence existed in `context-lake/client.py`'s
  `COSMOS_CONTAINERS`.

## Architecture

- **`auth.py`** (repo root): password hashing (bcrypt), JWT issuance/
  verification (`PyJWT`, HS256, 8h expiry), the static permission catalog
  (`PERMISSIONS`), and the default role catalog (`DEFAULT_ROLES`).
  Permissions are code-defined, not stored in Cosmos, since they're tied
  to actual code paths, not admin-editable data.
- **`context-lake/client.py` / `repositories.py`**: two new Cosmos
  containers, `users` and `roles` (partition key `/id` for both). Role
  assignments are embedded as a `role_keys` list on the user document
  rather than a separate `UserRole` join-table container — a deliberate,
  documented adaptation of the spec's suggested relational model to
  Cosmos/NoSQL idiom, not a literal implementation of it.
- **`azure-functions/api_blueprint.py`**: `require_permission()` /
  `require_any_permission()` are the real authorization gate — called at
  the top of every protected endpoint. They resolve a `Bearer` JWT to a
  user, re-read that user's current status and role grants from Cosmos on
  *every call* (never trusted from the token's own claims), and 401/403
  before any business logic runs. New endpoints: `POST /auth/login`,
  `GET /auth/me`, the full `/admin/users*`, `/admin/roles*`,
  `/admin/permissions` set. All 17 previously-unauthenticated internal
  endpoints were retrofitted with a `require_permission()` call; `health`,
  `candidates/by-email/{email}`, and `applications` remain intentionally
  anonymous (candidate-facing, pre-existing, documented gap unrelated to
  this feature).
- **Frontend**: `Login.jsx` now calls the real `POST /auth/login`
  (mock and real clients both implement it with matching semantics).
  `App.jsx`'s nav is driven entirely by the `permissions` array returned
  from `GET /auth/me`, not a role string — the old `NAV_BY_VIEW` demo
  role-selector model is gone. New `UserManagement.jsx` and
  `RoleManagement.jsx` admin pages, gated by `users.read`/`roles.read`
  like every other nav item.

## Data model

```
users    { id, username, password_hash, display_name, email,
           role_keys: [...], status: active|disabled,
           created_at, created_by, updated_at }
roles    { id (= role_key), role_key, display_name, description,
           is_system: bool, permission_ids: [...] }
```

Permissions themselves are not a Cosmos document — `auth.PERMISSIONS` is
the single source of truth, read by `GET /admin/permissions` for the UI.

## Roles and permissions

19 permissions across 10 modules (User Management, Role & Permission
Management, Candidate Management, Resume Upload and Parsing, Resume-JD
Matching, Interview Management, Document Verification, Analytics and
Reports, Audit Logs — screening propose/approve folded into Candidate
Management). Reconciled against this repo's actual modules: no
`themes.*` (no theme-management feature exists), no `matching.run` (JD
matching is automatic, not a manual trigger — `jds.read` covers the real
read-only surface).

There is **no separate Super Admin tier** — Admin is the single
top-level role, matching this app's original "bulk accept/reject always
needs admin approval" requirement (an earlier, separate feature built in
this project). An earlier revision of this feature introduced a
two-tier Super Admin/Admin split per the original IAM spec's suggestion;
that was removed after review, since this app only ever needed one
top-level administrative role.

| Role | Permissions | Note |
|---|---|---|
| Admin | all 19 | Top-level role: full user/role management, plus `screening.approve` — approves recruiter-proposed bulk decisions |
| Recruiter | 9 (has `screening.propose`, **not** `screening.approve`) | Proposes bulk decisions; only Admin approves them |
| Interviewer | 3 (`candidates.read`, `interviews.read`, `interviews.manage`) | Per-candidate assignment scoping not modeled — documented gap |
| Candidate | 0 | Uses the existing anonymous by-email status check, not this permission system |

## Administrative safeguards

- **No self-escalation**: `PUT /admin/users/{id}/roles` refuses if the
  target is the caller themselves, unconditionally.
- **Admin grant requires Admin**: creating a user with, or assigning, the
  `admin` role requires the actor to already hold it — a lesser custom
  role that happens to combine `users.create`/`roles.assign` cannot mint
  a peer Admin.
- **Protect the last active Admin**: disabling an account, or removing
  `admin` from one, is refused (409) if it would leave zero active
  Admins.
- **System roles are read-only**: Admin, Recruiter, Interviewer,
  Candidate cannot be edited via `PUT /admin/roles/{key}` — only a custom
  role created via `POST /admin/roles` can be.
- **No default Admin**: nothing in this app creates an Admin
  automatically. `context-lake/bootstrap_iam.py --create-admin
  <username>` is the one-time, explicit, password-prompted (never a CLI
  argument) way to create the first one.

## Audit logging

Every admin action (`user_created`, `user_updated`, `user_status_changed`,
`roles_assigned`, `role_created`, `role_updated`) and every login attempt
(`login_success`, `login_failed`) is written to the existing immutable
`audit_log` container, partitioned under a fixed `_system` id. Never logs
a password or password hash — only ids, usernames, and role keys.

## Tests

`tests/test_auth.py` (21 tests): password hashing, JWT round-trip/
tamper/expiry/wrong-secret, permission catalog consistency (Admin holds
every permission, Recruiter lacks `screening.approve`, Candidate has zero
grants, no `super_admin` role key exists).

`tests/test_api_blueprint.py` additions: `TestLogin` (correct/wrong
password, unknown username, disabled account, generic 401 message
against username enumeration), `TestRequirePermission` (missing/
malformed/garbage token, disabled user's token rejected, wrong
permission → 403, right permission → 200), `TestAdminPermissionsAndRoles`,
`TestAdminUsers`, `TestSetUserRoles` — covering every safeguard above as
an explicit negative test (self-escalation, last-Admin protection,
system-role immutability, a lesser custom role granting itself Admin).

All pre-existing tests were kept passing unchanged in behavior — a
shared `_req()` test helper attaches a fixture Admin's JWT by default, so
existing endpoint tests keep exercising their own logic rather than
being rewritten. **281 tests total, all passing.**

Manually verified live in the browser (mock mode): login/logout for all
4 roles, nav correctly scoped per role, user creation/edit/disable/role
assignment, custom role creation, system role read-only enforcement, a
recruiter proposing a bulk decision and Admin approving it end-to-end,
and a direct `window.CDPI_MOCK_API.listUsers()` call as a signed-in
recruiter correctly returning a 403 — proving the gate holds even
bypassing the UI.

## Known limitations (stated, not hidden)

- No URL routing in this app (no React Router) — `/admin/users` and
  `/admin/roles` are sidebar tabs within the existing single-page
  tab-switching model, not literal browser routes.
- Interviewer's `candidates.read`/`interviews.manage` is not scoped to
  "candidates assigned to this interviewer" — no assignment relationship
  exists in this repo's data model yet.
- `mockApiClient.js`'s permission catalog/default roles are hand-
  duplicated from `auth.py` (no bundler to share one source of truth
  between Python and a plain `<script>` file) — same convention already
  used for `DEFAULT_PIPELINE_STAGES`.
- Mock-mode user passwords are plaintext in a JS array — explicitly
  mock-only (no real backend behind it); the real backend
  (`apiClient.js` / `api_blueprint.py`) always bcrypt-hashes.
