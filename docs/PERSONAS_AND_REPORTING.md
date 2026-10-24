# Personas and Reporting — Phase 1

Implements the "Centralized RBAC" and "Phase 1 — Recruitment Operations"
sections of the CDPI Product Personas and Reporting Requirements doc.

## 1. Persona → role mapping (12 personas, 12 roles)

| Persona | Role key | Status |
|---|---|---|
| Candidate (P01) | `candidate` | Already existed |
| Recruiter (P02) | `recruiter` | Already existed |
| Hiring Manager (P03) | `hiring_manager` | **New** |
| Interviewer (P04) | `interviewer` | Already existed |
| Recruitment Operations Manager (P05) | `recruitment_ops_manager` | **New** |
| HR Director / TA Leader (P06) | `hr_director` | **New** |
| Recruitment Administrator (P07) | `recruitment_administrator` | **New** |
| Platform Administrator / Super Admin (P08) | `admin` | Already existed — this app has no separate Super Admin tier; `admin` already *is* the full-permission top-level role the persona describes |
| AI Configuration Administrator (P09) | `ai_configuration_administrator` | **New** |
| Azure / Cloud Administrator (P10) | `azure_cloud_administrator` | **New** |
| AI Operations / MLOps Engineer (P11) | `ai_operations_mlops` | **New** |
| Security, Risk & Compliance Auditor (P12) | `security_compliance_auditor` | **New** |

**Every permission referenced by the 8 new roles already existed** before this pass — built across the Azure Service Hub, RAG Configuration, Communications Configuration, and Secrets Management consoles earlier this session. This pass adds no new permission, only new *combinations* of existing ones, matching each persona's described responsibilities:

- `hiring_manager`: `candidates.read`, `interviews.read`, `analytics.read` — read-only, no scoring/workflow changes.
- `recruitment_ops_manager`: adds `interviews.manage`, `jds.read`, `audit.read`, `comms.delivery.read` on top of a Recruiter's read access.
- `hr_director`: `analytics.read` **only** — no candidate records, matching the persona's explicit "organization-level aggregated data" scope.
- `recruitment_administrator`: `interviews.manage`, `comms.config.read`, `comms.templates.manage` — recruitment-specific settings only.
- `ai_configuration_administrator`: the full `rag.*` set (5 permissions).
- `azure_cloud_administrator`: the full `azure.*` set (7 permissions) + `secrets.read`.
- `ai_operations_mlops`: `observability.read`, `observability.traces.read`.
- `security_compliance_auditor`: `audit.read`, `secrets.read`, `users.read`, `roles.read` — every permission is read-only, no `.write`/`.manage`/`.activate` anywhere, matching the persona's explicit read-only requirement.

## 2. Phase 1 reports — what's genuinely new vs. what already existed

The doc's own Section 18 "Recommended MVP Reporting Scope, Phase 1" lists 7 reports. Five already existed in some form before this pass; this pass adds the two that didn't exist anywhere:

| # | Report | Status |
|---|---|---|
| 1 | Recruitment Pipeline Report | Already exists (`Pipeline.jsx` + `/audit-log`) |
| 2 | Candidate Screening and Ranking Report | Already exists (`AdminReports.jsx`'s tier/score/rank charts, `Dashboard.jsx`) |
| 3 | Job-Wise Applicant Report | **Deferred, honestly** — see §3 below |
| 4 | Interview Schedule and Feedback Report | Already exists (`StageTimelineModal`, interview transcripts) |
| 5 | **Resume Processing Report** | **New this pass** |
| 6 | **Communication Delivery Report** | **New this pass** |
| 7 | Executive Recruitment Overview | Already exists (`/analytics/executive-summary` + `AdminReports.jsx`) |

### Resume Processing Report (new)

`GET /admin/reports/resume-processing`, gated by `observability.read`. Built from the **real** `document_extraction` stage spans already recorded in `logs/ai_traces.jsonl` by the AI Observability Center — not a separately-maintained counter (the doc's own "avoid duplicated reporting logic" requirement). Reports succeeded/failed counts, failure rate, and real recent failure detail. `pending_queue_depth` is explicitly `null` — this repo's batch-script pipeline (`ResumeParser.py` processes a folder) has no queue/backlog concept, so this is honestly reported as not applicable rather than invented.

### Communication Delivery Report (new)

`GET /admin/reports/communication-delivery`, gated by `comms.delivery.read`. Built from the real `comms_delivery_log` container (written by `_notify_candidate_whatsapp()`, the Communications Configuration pass's real WhatsApp delivery logging). **Only WhatsApp appears** — email has no delivery logging anywhere in this repo yet, not the legacy invite/report scripts, not even admin-triggered test sends. The report states this explicitly rather than showing a fabricated zero-row for email.

### Job-Wise Applicant Report — why this one is deferred, not built

Investigated and found a real data gap: candidates are not tagged with which job description they applied against (`GET /jds` lists job descriptions, but no `jd_id` field exists on a candidate record — confirmed in `api_blueprint.py`'s own `list_jds_endpoint` docstring, which flags this as pre-existing follow-up work). Building a "per-job applicant breakdown" today would mean either fabricating the association or silently assuming a single active job — both violate the doc's explicit "identify unavailable metrics rather than fabricate" principle. Left for a future pass once candidate records carry a real `jd_id`.

## 3. Tests

14 new tests in `tests/test_personas_and_reports.py`: all 12 persona roles exist with only real permission IDs; `hr_director`/`ai_configuration_administrator`/`azure_cloud_administrator`/`security_compliance_auditor` each verified to NOT exceed their documented scope (no candidate access leaking into `hr_director`, no secrets/azure access leaking into `ai_configuration_administrator`, etc.); both new report endpoints (real aggregation, real zeros on empty data, RBAC). Four pre-existing tests in other files were updated — not weakened — to reflect the new, intentional permission grants (e.g. `ai_operations_mlops` now legitimately holding `observability.read`, which a blanket "no non-admin role has this" assertion had to be narrowed to name the new exception explicitly). Full suite: **546 passed** (up from 532).

## 4. What this pass deliberately did not build

- The other ~95 reports in the doc's full catalogue (Sections 3–14) — Phase 1 scope only, per the doc's own phasing.
- Per-persona dashboards (Section 1.1's "a dashboard relevant to their responsibilities") — only the role/permission layer and the two new reports were built; dashboard layout work for each of the 8 new personas is separate frontend work, not started.
- Fine-grained resource scoping ("assigned jobs," "assigned vacancies," "assigned interviews") — this repo's data model has no job/vacancy/interview assignment concept yet; every role's access is still all-or-nothing within its permission set, not scoped to a subset of candidates/jobs. Stated as a known limitation, matching the existing `interviewer` role's own documented caveat.
- Cost per hire, post-hire retention, fairness/bias metrics, candidate satisfaction — explicitly called out in the doc itself (Section 19.5) as requiring instrumentation this repo doesn't have; not fabricated.
