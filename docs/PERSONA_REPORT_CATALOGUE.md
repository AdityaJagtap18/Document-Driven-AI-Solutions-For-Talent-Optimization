# Persona-to-Report Catalogue (Section 8)

Implements Section 8 of the CDPI Product Personas and Reporting
Requirements doc: "Creating One Representative Persona for Each of the 12
Persona Types." Extends [PERSONAS_AND_REPORTING.md](PERSONAS_AND_REPORTING.md)
(the Phase 1 role/report pass) with a full per-persona report catalogue
and demo-account mechanism.

**Source of truth:** [`persona_catalogue.py`](../persona_catalogue.py) at
the repo root, and `GET /admin/personas` (gated by `roles.read`). This
document is a snapshot of what that module computes from the REAL
`auth.DEFAULT_ROLES`/`auth.PAGES` data -- it is not a separate
authorization system, per the spec's explicit instruction. Run
`persona_catalogue.validate_persona_catalogue()` (also returned live by
the endpoint, as `validation_problems`) to re-check this snapshot is still
accurate after any future role/permission change.

## Explicit separation of concerns (Section 8.7)

**Persona definition, role mapping, and permission assignment are
complete for all 12 personas.** Report implementation is a SEPARATE,
independently-tracked axis -- a persona can be (and most are) correctly
configured while some of its required reports remain `planned` or
`data_dependent`. Nothing below claims "12-persona reporting" is complete
merely because 12 personas exist.

## Section 8.5 required fields, all 12 personas

| Persona | Role key | Allowed pages | Reports: available / planned / data-dependent | Data scope | Export |
|---|---|---|---|---|---|
| P01 Demo Candidate | `candidate` | 1 (`candidate_portal`) | 2 / 2 / 2 | Self only (own record, by-email) | No |
| P02 Demo Recruiter | `recruiter` | 6 | 5 / 1 / 1 | All candidates (no per-recruiter assignment model) | No |
| P03 Demo Hiring Manager | `hiring_manager` | 2 | 1 / 3 / 2 | All candidates (no per-vacancy assignment model) | No |
| P04 Demo Interviewer | `interviewer` | 2 | 2 / 4 / 0 | All candidates (no per-interviewer assignment model) | No |
| P05 Demo Recruitment Operations Manager | `recruitment_ops_manager` | 4 | 3 / 5 / 1 | All candidates, aggregate operational views | No |
| P06 Demo HR Director | `hr_director` | 1 (`admin_reports`) | 1 / 3 / 5 | Aggregate/org-level only -- **no candidate records** | No |
| P07 Demo Recruitment Administrator | `recruitment_administrator` | 2 | 4 / 3 / 0 | Configuration objects only -- no candidate records | No |
| P08 Demo Platform Administrator | `admin` | 13 (all) | 5 / 4 / 1 | Unrestricted (the one persona for which this is appropriate) | **Yes** |
| P09 Demo AI Configuration Administrator | `ai_configuration_administrator` | 2 | 4 / 2 / 5 | AI/RAG configuration only -- no candidate PII, no secrets, no Azure | No |
| P10 Demo Azure / Cloud Administrator | `azure_cloud_administrator` | 2 | 7 / 2 / 3 | Azure config + secret presence only -- never secret values, never candidate data | No |
| P11 Demo AI Operations / MLOps Engineer | `ai_operations_mlops` | 1 (`ai_observability`) | 8 / 0 / 4 | Aggregate AI telemetry + trace detail -- no candidate records, no config-write | No |
| P12 Demo Security, Risk & Compliance Auditor | `security_compliance_auditor` | 5 | 6 / 1 / 5 | Audit/compliance evidence only -- read-only everywhere | No |

"Allowed pages" and "Restricted pages" (the full complementary list, 12 /
13 pages minus the ones above per persona) are **computed**, not
hand-typed, from `auth.PAGES`' `required_permission` against each
persona's actual role -- see `persona_catalogue.allowed_pages()`. The full
per-persona page lists and the full report-name/status/gating-permission
breakdown (102 report slugs total across the catalogue) are in
`persona_catalogue.REPORTS`/`PERSONAS`, and returned verbatim by
`GET /admin/personas`.

Export permission is `False` everywhere except Platform Administrator
because **no export mechanism (CSV/PDF download, etc.) is implemented
anywhere in this repo today** -- recorded honestly as absent rather than
assumed available because a role could theoretically read the underlying
data.

## Validated guardrails (Section 8.3, 8.6)

Enforced by `persona_catalogue.validate_persona_catalogue()` and exercised
in `tests/test_personas_and_reports.py::TestPersonaCatalogue`:

- Every persona's `role_key` resolves to a role that actually exists in
  `auth.DEFAULT_ROLES` (no persona is an "orphan" with no backing role).
- Every report a persona requires is cross-checked against the real
  permission its role holds -- a report marked `available` is only ever
  reported `authorized: true` if the gating permission is genuinely
  granted. This is the mechanism that would catch a future accidental
  permission removal silently breaking a persona's access.
- **Explicit spec examples, directly tested:**
  - HR Director demo persona can reach `admin_reports` (Executive
    Recruitment Overview) but is denied `dashboard` (no `candidates.read`)
    -- `TestPersonaCatalogue::test_golden_hr_director_sees_executive_overview_not_candidate_dashboard`.
  - Interviewer demo persona sees interview-related pages but not
    `admin_reports`/`recruiter_reports` (no `analytics.read`/
    `screening.propose`) -- `test_golden_interviewer_sees_only_assigned_interview_pages_not_recruitment_reports`.
  - Candidate demo persona has zero permissions and reaches only
    `candidate_portal` -- `test_golden_candidate_is_self_scoped_only`.
- No persona except Platform Administrator receives `export_permission`
  (adversarial test, catching an accidental over-grant).

## Demo accounts (opt-in, synthetic/test data only)

`repositories.ensure_demo_personas()` creates one account per persona
(`demo_candidate`, `demo_recruiter`, ... `demo_security_compliance_auditor`),
each flagged `is_demo_persona: true` and using a `@example.invalid` email
-- **never created automatically**. Run explicitly via:

```bash
python context-lake/bootstrap_iam.py --create-demo-personas
```

Generated passwords are random (`secrets.token_urlsafe(16)`) and printed
**once**, at creation -- never stored in plaintext, never retrievable
again, same one-time-reveal convention as the Secrets Management
console's `PUT /admin/secrets/{key}`. Re-running the command is a no-op
for any demo account that already exists (idempotent, never
password-resets an existing one). These are demonstration/test accounts
only -- per the spec's explicit constraint, they are not real users unless
real accounts are separately created through the normal
`POST /admin/users` flow.

## What this pass deliberately left as `planned`/`data_dependent`, not built

Per Section 8.7's explicit separation of persona-configuration completion
from report-implementation completion, the ~45 `planned` and ~33
`data_dependent` report slugs in `persona_catalogue.REPORTS` are
**unchanged from the internal analysis this pass started from** --
building out the missing reports themselves (new endpoints, new data
capture, new aggregation logic) is explicitly out of scope for this pass,
which is RBAC/catalogue/documentation only. Each slug's `note` field
states the specific reason (no feature exists yet vs. a genuine missing
data/instrumentation gap) rather than leaving the distinction implicit.
