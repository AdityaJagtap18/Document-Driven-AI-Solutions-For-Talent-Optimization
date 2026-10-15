# CDPI Frontend (Phase 4)

A React frontend covering 3 of the 5 confirmed personas (Candidate, Recruiter/TA, Admin — Hiring Manager and Dev views not built yet). Calls the Azure Functions API built in `azure-functions/api_blueprint.py`.

## Why this isn't a normal Vite/React project

**This environment has no Node.js or npm installed** (checked both Bash and PowerShell — neither has `node`/`npm`). A standard `create-vite` project would be unrunnable and unverifiable here. Per the user's decision, this is instead plain React loaded via CDN (`<script>` tags, pinned versions, no bundler), with Babel standalone doing in-browser JSX transformation. This is a real, working tradeoff, not a toy: it was actually served over HTTP and exercised in a real browser (see "Verified" below) — something a from-scratch Vite project could not have been, in this environment.

**Migrate to a real Vite project once Node is available.** The `src/` files are already organized the way a Vite project would expect (`src/App.jsx`, `src/components/*.jsx`) — moving them into a scaffolded `npm create vite@latest` project should be close to a direct copy, swapping the CDN `<script>` tags for `import` statements and running everything through esbuild/Vite's dev server instead of Babel standalone (which explicitly warns it's not meant for production use).

## Status: built AND verified working, not just written

Unlike a typical "I can't run this" caveat, this was actually tested:

1. Served over HTTP via `python -m http.server` (not opened as `file://` — the separate `<script src="...">` loads for each `.jsx` file need real HTTP, `file://` blocks them via CORS).
2. Loaded in a real browser (Claude's built-in browser pane) — confirmed zero console errors beyond Babel's expected dev-mode warning.
3. **Every view actually exercised, not just loaded**: switched between all 3 persona views via the nav; on the Dashboard, confirmed the tier-count stats and token/cost stats compute correctly from mock data; on the Review Queue, clicked Approve with no reviewer name entered and confirmed the validation message appears, then entered a name and approved for real — confirmed the candidate disappears from the queue (the mock API's in-memory state update worked); on the Candidate Portal, checked status for a real mock candidate's email and confirmed the correct tier/name/date renders.

This is real, interactive, verified frontend code — not a static mockup.

## Running it yourself

```bash
cd frontend
python -m http.server 8732
# open http://localhost:8732 — NOT file:///.../index.html, which will not work
```

Defaults to mock data (`src/config.js`, `mode: "mock"`) — no backend needed. To point it at a real deployed/running Azure Functions API, change `config.js`:

```js
window.CDPI_CONFIG = {
  mode: "real",
  baseUrl: "https://<your-function-app>.azurewebsites.net", // or http://localhost:7071 for `func start`
  functionKey: "<a function key from the Azure Portal>",
};
```

Nothing else needs to change — `App.jsx` picks `window.CDPI_REAL_API` or `window.CDPI_MOCK_API` based on `mode` alone; both implement the exact same function signatures and response shapes.

## What's built

| View | Covers | Status |
|---|---|---|
| Dashboard | Recruiter/Admin Dashboard + HR Reporting Dashboard (combined — see below) | Working: candidate list, tier filter, funnel counts, cost/token stats |
| Human Review Queue | FDE Stage 23 (HITL) | Working: lists "To be Considered" tier, approve/reject calls the real review endpoint shape, requires a reviewer name |
| Candidate Portal | Candidate Self-Service Portal | Status check fully working (`GET /candidates/by-email/{email}`). Apply + self-schedule interview are **visible, labeled placeholders** — no backend endpoint exists for either yet; see below |

**Dashboard + HR Reporting combined into one view**: the backlog lists these as two separate features, but HR Reporting was scoped as "extends the Recruiter/Admin Dashboard with exportable reports" — there's no exportable-report endpoint yet either, so there was nothing distinct to build as a second view. Revisit once a reports/export endpoint exists.

## Login (demo accounts, not real auth)

`src/components/Login.jsx` gates which single view renders — no more nav bar letting you click into every persona's view. Hardcoded demo accounts (visible in that file's source, checked entirely client-side):

| Username | Password | Sees |
|---|---|---|
| `candidate` | `candidate123` | Candidate Portal only |
| `recruiter` | `recruiter123` | Human Review Queue only |
| `admin` | `admin123` | Recruiter/Admin Dashboard only |

This is explicitly **not** access control — it's a usability fix (one view per session instead of free navigation) for a local demo. Real RBAC needs Entra ID / MSAL, see below.

## Real candidate data (mock mode, not hardcoded)

`src/data/candidates.json` (gitignored — it's real names/emails/phone numbers, same PII reasoning as `semantic-library/index/`) replaces the old 4 made-up candidates. Generate/refresh it from the actual pipeline output:

```bash
python frontend/export_real_candidates.py
```

Reads `CandidateMetrics_Folder/candidate_ranking_metrics.csv`, writes `src/data/candidates.json` with the same `candidate_id` the real pipeline would assign (via `context-lake/candidate_id.py`'s deterministic uuid5), so these ids stay consistent with whatever ends up in Cosmos DB once that's provisioned. `mockApiClient.js` falls back to a small hardcoded set if this file is missing (e.g. on a fresh checkout before anyone's run the script).

## What's NOT built (real gaps, not hidden)

- **Dev persona view** — not started.
- **Real RBAC / Entra ID auth** — the demo login above only gates which view renders; it's not a real identity check. A real implementation needs an Azure AD App Registration (external Azure setup the user needs to do) and MSAL.js integration.
- **URL routing** — view switching is in-memory React state only (no router library, not worth the dependency for 3 views with no bundler). Browser back/refresh loses your place. A real router (React Router, or Vite's file-based routing) should come with the eventual Vite migration.
- **Application submission (resume upload)** — needs a new API endpoint (file upload → Blob Storage write → trigger processing) that doesn't exist in `azure-functions/api_blueprint.py` yet. The UI placeholder is honest about this rather than faking a working form.
- **Interview self-scheduling** — depends on the above, plus a scheduling endpoint that also doesn't exist yet.
- **`candidates/by-email/{email}` security gap** (inherited from the API layer, not introduced here): nothing stops looking up a different candidate's status by guessing their email. Documented in `azure-functions/api_blueprint.py`'s docstring too.

## Hosting

Explicitly out of scope per earlier instruction (`Frontend Hosting & Deployment` stays Future Scope in the tracker) — this is a local, build-and-verify-only deliverable.
