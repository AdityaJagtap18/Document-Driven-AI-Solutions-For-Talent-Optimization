# Implementation Log

Chronological record of this loop's actual work. Each entry: what was done, files touched, exact test commands/results, defects found, fixes made, decisions.

---

## 2026-10-25 — Session resume + test-baseline recovery

**Context:** Previous session ended mid-recovery from a server-side safety-classifier outage (Bash/PowerShell/Write all returning transient errors). This session resumed per the user's explicit recovery instructions.

**Step 1 — Recovery verification:**
- Read-only check (`Glob docs/fde/*`) succeeded immediately.
- Minimal mutation check: `git status --short` — succeeded, confirming execution is available again.
- `git status --short` output confirmed: all Phase 1 (Tasks 1–7) changes present and unmodified; no unexpected user changes; nothing reset, stashed, or discarded.
- Confirmed repo root (`.../worktrees/fde-features-azure-migration-a07cea`) and branch (`claude/fde-features-azure-migration-a07cea`).

**Step 2 — Baseline uncertainty resolution:**
- Command: `python -m pytest "tests/test_rag_config.py::TestPreviewRagConfigEndpoint::test_adversarial_rate_limited_second_call" -q`, run 5× in isolation.
- Result: **FAILED 5/5**, consistently, each in ~12.2–12.8s. Not a flake — deterministic under current environment conditions.
- Diagnosis (via direct code reading, not speculation):
  - `preview_rag_config_endpoint` (azure-functions/api_blueprint.py) calls `_audit_admin_action()` unconditionally on every successful request.
  - `_audit_admin_action()` calls `repositories.append_audit_log()`, which was **not mocked** in this test (confirmed by reading the full `TestPreviewRagConfigEndpoint` class — the only 3 of its 6 tests that reach this call point were missing the mock).
  - `append_audit_log()` → `context-lake/client.py`'s `get_container()` → `secrets_loader.get_secret("COSMOS_ENDPOINT")` / `get_secret("COSMOS_KEY")`. `AZURE_KEY_VAULT_URL` is configured in this environment; Key Vault auth is currently failing (expired Azure CLI Temporary Access Pass — confirmed via the captured `DefaultAzureCredential` chain-failure warnings in the test's stdout). Each secret lookup takes ~6 real seconds to exhaust the credential chain before falling back.
  - `_audit_admin_action()`'s own try/except swallows the resulting exception, so the request still returns 200 — but ~12s of real wall-clock time elapsed first, which exceeded the rate limiter's 5-second window (`_RAG_PREVIEW_MIN_INTERVAL_SECONDS`), so the second of the test's two back-to-back calls was never rate-limited.
- **Fix (smallest justified change):** added `monkeypatch.setattr(repositories, "append_audit_log", lambda entry: entry)` to `test_golden_retrieval_only_preview`, `test_adversarial_rate_limited_second_call`, and `test_golden_generation_skipped_when_insufficient_evidence` in `tests/test_rag_config.py` — matching the file's own stated isolation convention. Did not touch `_rate_limited()` or the endpoint itself; did not weaken the assertion.
- **Regression test added:** new `TestRateLimitedHelper` class (3 tests) in `tests/test_rag_config.py`, testing `_rate_limited()` directly with a fake clock (`monkeypatch.setattr("time.monotonic", ...)`), independent of any endpoint or real I/O — covers: second call within window is limited; call after window elapses is not limited; different actors don't share the limit.
- **Verification:**
  - `test_adversarial_rate_limited_second_call` alone, 3× isolated reruns after the fix: **PASSED 3/3**, 0.15–0.16s each (vs. ~12s before).
  - `TestPreviewRagConfigEndpoint` + `TestRateLimitedHelper` together: **9 passed in 0.16s**.
  - Full `tests/test_rag_config.py` module: **61 passed in 98.98s** (0:01:38) — exit code 0.
  - Full backend suite (`python -m pytest tests/ -q --ignore=tests/e2e`): launched, running in background — result to be recorded in the next entry.

**Files changed:** `tests/test_rag_config.py` only (3 lines added for mocks + 1 new test class, ~25 lines). No application code changed — this was purely a test-isolation fix.

**Decision:** classified this as a genuine test-isolation defect, not dismissed as environmental noise, per explicit instruction — confirmed with reproducible evidence before concluding anything, and the fix targets the actual root cause (missing mock) rather than papering over the symptom (e.g., raising the rate-limit window or adding a sleep would have been the wrong fix).

---

## 2026-10-25 — F-19: RAG evaluation framework (minimal slice)

**Prompt read in full:** `features/19_comprehensive-rag-evaluation-framework-using-llm-as-judge-and-golden-question-datasets.md` (scratchpad path in IMPLEMENTATION_STATUS.md). The raw prompt describes a portfolio-scale framework (multi-config comparison, scheduled runs, dashboards, reranker/embedding sweeps, regression gates across every RAG pipeline). Deliberately scoped down to the smallest real, defensible slice, per this loop's own "do not create an elaborate feature simply to increase the count of completed items" rule — documented explicitly in `rag_evaluation.py`'s own module docstring, not silently under-delivered.

**Existing infrastructure checked before building anything new:** `rag_preview.py` (real retrieval/generation against the on-disk index), `rag.preview.run` permission (reused, no new permission), `rag_config.sanitize_for_prompt()` (reused for the judge prompt, same F-10 discipline). No existing evaluation utilities/datasets found in the repo — confirmed via grep before starting, not assumed.

**Golden dataset construction:** investigated `semantic-library/index/resume-chunks.json` directly (3.1MB real index, dozens of real-looking but synthetic resumes) via targeted grep rather than guessing. Found this is a generic/mixed-industry synthetic dataset (surveyor, insurance claims handler, telecom, alongside real IT/software profiles) — not all tech-focused. Verified, for each of 4 golden questions, the EXACT real expected match(es) by grep before writing them into `rag_evaluation.GOLDEN_QUESTIONS`:
- RAGEVAL-001 (Terraform/Jenkins/GitHub Actions): exactly 1 resume (`Priyal_Kalal_resume.pdf`) mentions Terraform.
- RAGEVAL-002 (REST APIs + LLM-backed features in Python): exactly 1 resume (`alex_morgan_resume.pdf`) mentions "LLM" at all.
- RAGEVAL-003 (Kubernetes + Docker): exactly 5 resumes match, used as an any-of-5 hit criterion.
- RAGEVAL-004 (deep-sea marine biology + particle physics): 0 resumes match anything in this domain — the deliberate "unanswerable" case.
No raw chunk text, email, phone number, or LinkedIn URL copied into the dataset — only the `resume_name` identifier, already a committed index key in this repo.

**Implementation:** `rag_evaluation.py` (retrieval hit-rate evaluation, groundedness-judge prompt construction + structured-output validation with a typed `GroundednessJudgeError`, never a bare JSON/KeyError escaping to a caller); `azure-functions/api_blueprint.py` — new `POST manage/rag-evaluation/run` (reuses `rag.preview.run`, own rate limiter `_RAG_EVALUATION_MIN_INTERVAL_SECONDS=30` since this is a heavier multi-LLM-call operation, kept separate from the plain preview endpoint's limiter per that function's own documented convention).

**Tests:** `tests/test_rag_evaluation.py` (18 tests — hit/miss logic, unanswerable-category logic, retrieval-failure handling, dataset PII-safety checks, judge-prompt sanitization, judge-response validation) + `tests/test_api_blueprint.py::TestRunRagEvaluationEndpoint` + `TestGroundednessEvaluationHelper` (10 tests — auth, rate-limiting, opt-in groundedness, malformed-judge-response handling). All mocked, explicitly distinguished from the live demonstration below.

**Live demonstration (real, not mocked) — exact commands and results:**
- `python -c "import rag_evaluation; ...rag_evaluation.run_retrieval_evaluation()..."` against the real index: `{"total": 4, "passed": 3, "hit_rate": 0.75, ...}`. RAGEVAL-001/002/003 all hit their real expected matches. RAGEVAL-004 scored top_score=0.4828, above the originally-set `_NO_MATCH_SCORE_CEILING=0.35` ceiling, so it reported FAILED.
- Follow-up comparative run printing top_score for all 4 (after adding that field to the non-unanswerable branch too): RAGEVAL-001=0.6283, RAGEVAL-002=0.7768, RAGEVAL-003=0.3368, RAGEVAL-004=0.4828.
- **Finding:** genuine-match and genuine-non-match score ranges overlap in this embedding space (RAGEVAL-003, a real match, scored lower than RAGEVAL-004, the one real non-match). **Decision: did not raise the ceiling to force a pass** — that would fit a threshold to a single data point and hide a real finding, which this engagement's own "do not use thresholds to hide a real finding" rule (established during the rate-limiter investigation) explicitly treats as prohibited. Left the ceiling at its originally-documented value; the live result honestly reports 3/4. Documented as a real, open limitation in `docs/fde/DECISIONS_AND_BLOCKERS.md` item 5, not silently absorbed.
- Also attempted a live groundedness-judge run and live Functions-host-backed browser demo; the Functions host itself failed to start 3 consecutive times with a real Azure Storage "Request date header too old" authentication error (a local environment issue, not application code) — documented, not retried further; the retrieval evaluation above was run via direct Python invocation instead, which doesn't need the Functions host.

**Full regression suite:** `python -m pytest tests/ -q --ignore=tests/e2e` → **763 passed, 0 failed, exit 0**, 756.34s. Exact match to 732 (Phase 1 baseline, including the then-known rate-limiter failure) + 3 (`TestRateLimitedHelper`) + 28 (F-19) = 763.

**Status:** VERIFIED_WITH_LIMITATIONS. See IMPLEMENTATION_STATUS.md for the full limitations list (no frontend UI yet, groundedness checked on one question only, score-ceiling calibration gap, not the full framework).

---

## 2026-10-25 — Patch Notes page (user-requested, mid-loop)

User explicitly requested a "patch notes section where all features add and yet to add will be shown." Added `frontend/src/components/PatchNotes.jsx` — hand-maintained, two-section page ("Shipped" / "Planned / not yet added") sourced from this tracker's own real status, not live-generated from a backend (patch notes are inherently a curated, versioned record, same convention as a real CHANGELOG — updating this file by hand alongside `docs/fde/IMPLEMENTATION_STATUS.md` is the intended workflow, not a parallel auto-generated source that could drift from it).

New `patch_notes` PAGES entry added to `auth.py`, reusing the existing `analytics.read` permission (no new permission invented). Per `sync_pages()`'s own secure-defaults rule, this page starts with zero granted access on the real backend until an admin runs Sync + explicitly grants it — documented in the PAGES entry's own description, not worked around.

Verified live in the browser (mock mode, since the real Functions host still can't start — see the F-19 entry above): both sections render with correct status badges; confirmed real repo evidence underpins every "Shipped" claim (cross-checked against this file and IMPLEMENTATION_STATUS.md while writing the entries, not invented from memory).

Files: `frontend/src/components/PatchNotes.jsx` (new), `frontend/src/App.jsx` (nav entry + render), `frontend/src/styles.css` (`.patch-note-*` classes), `frontend/index.html` (script tag, cache-bust), `auth.py` (PAGES entry), `frontend/src/mockApiClient.js` (mirrored MOCK_PAGES_CATALOG entry for parity with the real registry).

---

## 2026-10-25 — F-21: AI evaluation/QA framework (minimal slice)

**Prompt read in full:** `features/21_comprehensive-ai-evaluation-quality-assurance-and-continuous-monitoring-framework.md`. Same portfolio-scale raw scope as F-19; deliberately scoped down, per the loop's "do not create an elaborate feature simply to increase the count of completed items" rule and the roadmap's own prior recommendation ("one evaluation gate on the JD-match scoring pipeline, reuses F-19's judge work directly").

**Design:** `ai_evaluation.py` — (1) `validate_structured_extraction()`, a deterministic rule-based validator against the REAL contract `rag_config.DEFAULT_MERGE_EXTRACTION_TEMPLATE` defines (not invented) — checks Resume Strength Score is numeric 0-10, Candidate Type is one of the 3 real values, Employment Gaps Detected is boolean, JD Match is percentage-shaped, Missing Keywords/Top Matching Keywords/Projects have the real nested shapes resume_scoring.py's functions assume. Documented why this matters: several resume_scoring.py functions (`skill_score`, `penalty_score`, `semantic_match_score_or_fallback`) wrap field reads in bare try/except and silently return 0 on a parse failure — a malformed extraction doesn't crash, it silently under-scores a real candidate. (2) `build_match_quality_judge_prompt()` + genuine reuse (not duplication) of `rag_evaluation.parse_judge_response`/`GroundednessJudgeError` for the LLM-as-judge half — confirmed by a test asserting object identity (`ai_evaluation.rag_evaluation.parse_judge_response is rag_evaluation.parse_judge_response`).

**Endpoint:** `POST manage/ai-evaluation/run`, reuses `rag.preview.run` (no new permission). Always runs the structured-output gate against every real candidate (or a specific one); the match-quality judge is opt-in, requires both `candidate_id` and `jd_id` (real LLM cost), own 30s rate-limit window separate from F-19's.

**Tests:** `tests/test_ai_evaluation.py` (24) + `tests/test_api_blueprint.py::TestRunAiEvaluationEndpoint` + `TestMatchQualityEvaluationHelper` (11) = 35 new, all mocked. One test (`test_edge_optional_fields_absent_do_not_error`) initially failed because its own fixture omitted "Missing Keywords" — investigated before assuming a validator bug: confirmed the real extraction template always requests this field (even as empty lists), so its absence IS a genuine contract violation the validator is correctly catching. Fixed the test fixture, not the validator, and added a new adversarial test (`test_adversarial_missing_keywords_field_absent_is_flagged`) proving the validator's correctness on this exact case.

**Infrastructure incident during this task (see DECISIONS_AND_BLOCKERS.md item 8 for the full resolved writeup):** the full regression suite appeared to hang (~21 minutes, 0.56s CPU accumulated) partway through verification. Killed the suspect process and relaunched with live, unbuffered logging to isolate where it was stalling -- turned out to be a false alarm (a bad grep pattern falsely "detected completion" early, and the actual process I killed was likely a stale/unrelated one; the original run had in fact completed cleanly in the background the whole time). Separately, live verification against real Cosmos failed outright ("authorization token is not valid"), and the local Functions host failed to start 3 times with a real Azure Storage auth error. Investigated rather than assumed: the user re-ran `az login --use-device-code` (fixed the AAD/Key-Vault/Cosmos path), but the Functions host STILL failed identically afterward, which was the signal this wasn't a simple expired-session issue. Direct clock comparison (`date -u` vs. an external server's real `Date` header) confirmed the actual root cause: this VM's system clock was ~5.5 hours behind real time. Attempted self-fix (`w32tm /resync /force`) failed on permissions (not a local admin); the user fixed the clock directly. Verified resolved end to end afterward.

**Live verification (real, not mocked) — exact commands and results, after the clock fix:**
- `curl .../api/health` → `{"status": "ok"}`; `curl .../api/auth/login` → real JWT issued.
- `curl -X POST .../api/manage/ai-evaluation/run -d '{}'` → `{"structured_output_gate": {"total": 1, "passed": 0, "pass_rate": 0.0, "per_candidate": [{"candidate_id": "0e977444-...", "passed": false, "violations": ["Resume Strength Score must be a number 0-10, got '8.5'"]}]}, "match_quality_evaluation": null}`. **Real finding:** the one real candidate's stored `Resume Strength Score` is the string `'8.5'`, not a JSON number — a genuine, previously-undetected data-quality defect in the real extraction pipeline, caught exactly as this gate is meant to. Not fixed here (out of scope for an evaluation-gate slice) — flagged for whoever next touches `ResumeParser.py`'s merge-extraction step.
- `curl -X POST .../api/manage/ai-evaluation/run -d '{"candidate_id":"0e977444-...","jd_id":"jd_junior_swe_ai_doc_processing","include_match_quality":true}'` → real judge output: `{"match_quality_score": 4, "rationale": "...the missing keyword 'Java' slightly detracts from a perfect match, making the 90% claim slightly optimistic but still largely supportable."}` — substantive, real LLM reasoning grounded in the real extracted profile and real JD text.

**Full regression suite, confirmed twice independently (different invocations, same result):** `python -m pytest tests/ -q --ignore=tests/e2e` → **798 passed, 0 failed, exit 0** (740.34s and 603.71s across the two runs). Exact match to 763 + 35 = 798.

**Status:** VERIFIED_WITH_LIMITATIONS. See IMPLEMENTATION_STATUS.md.

---

## 2026-10-25 — F-14: Audit evidence and candidate data erasure (Cosmos-scoped slice)

**Prompt read in full:** `features/14_end-to-end-audit-evidence-traceability-and-candidate-data-erasure-management.md`. Same portfolio-scale raw scope pattern as F-19/F-21; scoped to the roadmap's own prior recommendation — a Cosmos-scoped erasure first slice, with every other real store explicitly documented as a known gap.

**Real data inventory established before writing any code** (`context-lake/client.py`'s `COSMOS_CONTAINERS`, cross-checked against `repositories.py`'s actual usage of each):
- Candidate-keyed, real deletion implemented: `candidates` (/candidate_id), `interview_transcripts` (/candidate_id), `scoring_history` (/candidate_id), `comms_delivery_log` (/correlation_id — confirmed via `record_comms_delivery()`'s own docstring that correlation_id IS the candidate_id).
- `audit_log` (/candidate_id): deliberately RETAINED, not deleted — immutable audit evidence; documented as a legitimate retention exception, not an oversight, matching the feature prompt's own text distinguishing "actual deletion" from "retention required by law or another valid obligation."
- `bulk_decisions` (/id, `candidate_ids` array field): a real entanglement risk — a batch's candidate_ids array is shared with OTHER real candidates, so whole-document deletion would erase people never asked to be erased. Detected via a real cross-partition `ARRAY_CONTAINS` query and reported, never auto-mutated — out of scope for a minimal slice to safely partially redact.
- Explicitly out of scope, stated in every real API response (not silently omitted): Blob Storage (raw resume PDFs), the real semantic-library index (resume-chunks.json — confirmed this holds real chunk text/embeddings, a real un-erased PII surface), legacy CSVs, local JSON artifact folders, local JSONL observability logs.

**New permission:** `candidates.erase` — a genuinely NEW permission (not reused from an existing one), since no existing permission (`candidates.update`, `audit.read`) is actually a deletion authority. Admin-only by default, deliberately not granted to any other role given the irreversible nature of real data deletion.

**Implementation:** `candidate_erasure.py` — `locate_candidate_data()` (read-only discovery/preview, zero mutation) and `erase_candidate()` (the real deletion, which RE-QUERIES every erased container afterward to verify 0 records remain — never trusts a delete call's own success alone). Backing primitives added to `context-lake/repositories.py`: `delete_candidate()`, `delete_interview_transcripts_for_candidate()`, `delete_scoring_history_for_candidate()`, `delete_comms_deliveries_for_candidate()`, `find_bulk_decisions_referencing_candidate()` (read-only).

**Endpoints:** `GET manage/candidates/{id}/erasure` (preview, read-only), `POST manage/candidates/{id}/erasure` (real deletion, requires explicit `{"confirm": true}` body field — a bare POST is refused with 400 before touching any data). The erasure action itself is audited via the existing `_audit_admin_action()` pattern (candidate_id + real per-container erased counts, never raw PII).

**Tests:** `tests/test_candidate_erasure.py` (9) + `tests/test_api_blueprint.py::TestCandidateErasurePreviewEndpoint` + `TestRunCandidateErasureEndpoint` (9) = 18 new, all mocked. One test bug found and fixed during this task (missing `body=None` kwarg on a `func.HttpRequest` construction for a GET test) — not a defect in the feature code itself.

**Live verification (real, not mocked) — deliberately read-only:**
- `GET .../erasure` for the real demo candidate (`0e977444-...`) initially returned 403 — a REAL, expected finding: the persisted `admin` role document in Cosmos was seeded before `candidates.erase` existed, the same `ensure_default_roles()` insert-only gap found and fixed earlier this engagement. Ran the real `POST manage/roles/sync` endpoint (built in that earlier fix) — confirmed `{"updated": ["admin"]}` — then retried successfully.
- Real preview result: `{"will_erase": {"candidates": 1, "interview_transcripts": 0, "scoring_history": 0, "comms_delivery_log": 0}, "retained": {"audit_log": {"count": 11, ...}}, "found_entangled_not_modified": {}, "out_of_scope_stores": [...]}` — correctly reflects this candidate's real journey (stage history lives in `audit_log` via `StageTimelineModal`, not a separate `interview_transcripts` container).
- **The actual destructive `POST .../erasure` was deliberately NOT run** against this real candidate — it's the only real demo data this whole engagement has used for F-06/F-08/F-19/F-21 live verification, and real, irreversible deletion of it requires the user's own explicit authorization, not something to trigger unprompted mid-loop. The erasure PATH itself is still fully verified via the 9 mocked tests (including a real-provider-failure case and a case proving verification catches an incomplete deletion).

**Full regression suite:** 798 passed (confirmed as the pre-F-02 baseline by the suite run logged in the next entry).

**Status:** VERIFIED_WITH_LIMITATIONS. See IMPLEMENTATION_STATUS.md.

---

## F-02 — Tokenomics calculator and usage analytics (minimal slice)

**Scope decision:** the roadmap identified this as mostly already real (`observability.record_llm_call()` + `model_pricing.py`, already feeding the existing AI Observability Center's all-time summary). Inspected `_llm_observability_summary()` in `azure-functions/api_blueprint.py` directly and confirmed it has no period concept and no budget concept at all. Scoped this slice to exactly those two real gaps: (1) a period-scoped (current UTC calendar month) usage aggregation, and (2) a real, versioned, admin-configurable budget/warning-threshold check — not a rewrite of the existing all-time view.

**Backend built:**
- `tokenomics.py` (new): `DEFAULT_CONFIG`, `validate_config()`, `_is_current_utc_month()`, `compute_monthly_usage()` (filters `llm_observability.jsonl` to the current month; tracks `unknown_cost_call_count` explicitly, never folding a missing cost in as $0), `check_budget_status()` (`not_configured`/`ok`/`warning`/`exceeded`, never divides by zero, never fabricates a percentage with no budget set).
- `context-lake/client.py`: `tokenomics_config` + `tokenomics_config_history` containers (`/id`), same Draft → Activate → History pattern as `rag_configurations`.
- `context-lake/repositories.py`: `get_active_tokenomics_config`, `get_draft_tokenomics_config`, `save_draft_tokenomics_config`, `activate_tokenomics_config`, `record_tokenomics_config_change`, `list_tokenomics_config_history` — mirrored line-for-line from the proven `rag_config`/`comms_config` repository functions, not reinvented.
- `auth.py`: new `tokenomics.config.write` permission (admin-only by default; viewing reuses the existing `analytics.read`, same as every other observability view).
- `azure-functions/api_blueprint.py`: `import tokenomics`; 5 new endpoints — `GET`/`PUT manage/tokenomics-config`, `POST manage/tokenomics-config/activate`, `GET manage/tokenomics-config/history`, `GET manage/tokenomics/status` (the real-time usage+budget check, reading `observability.LOG_PATH` via the existing `_read_jsonl()` helper).

**Frontend built:** per the "extend the existing page, don't invent a disconnected one" discipline (cost/token data already lives in AI Observability Center) — added a "Budget & Alerts" tab there (`TokenomicsBudgetPanel` in `AIObservabilityCenter.jsx`): shows this month's spend/calls/tokens/budget status, a budget-config form (blank budget = disabled), Save as Draft / Activate Draft buttons, and the currently-active config. Added matching methods to `apiClient.js` (real) and `mockApiClient.js` (mock, reusing the existing illustrative `MOCK_AI_TRACES`-derived totals as "this month's" spend, documented in the mock method's own comment as a simplification since mock mode has no real per-call timestamps to filter by). Added `.stat-tile-warn` to `styles.css` (reused the existing `--accent-orange` token, no new color introduced). Bumped `frontend/index.html`'s cache-bust to `v=55` after all content edits (not before — the established ordering discipline from the Patch Notes work).

**Tests:** `tests/test_tokenomics.py` (27, pure-function: `validate_config`, `_is_current_utc_month`, `compute_monthly_usage`, `check_budget_status`) + `tests/test_api_blueprint.py` (18 new endpoint tests: `TestGetTokenomicsConfigEndpoint`, `TestSaveTokenomicsConfigDraftEndpoint`, `TestActivateTokenomicsConfigEndpoint`, `TestGetTokenomicsConfigHistoryEndpoint`, `TestGetTokenomicsStatusEndpoint`) = 45 new, all mocked. Two test-construction bugs found and fixed during this task (missing `body=None` kwarg on two `func.HttpRequest` GET constructions) — not defects in the feature code itself, same class of bug as the F-14 fix.

**Full regression suite:** `python -m pytest tests/ -q --ignore=tests/e2e` → **861 passed, 0 failed, exit 0**, 239.91s (0:03:59). Exact match: 798 + 18 (F-14) + 45 (F-02) = 861. (A same-session run without the e2e ignore flag showed 1 additional failure — `tests/e2e/test_azure_readiness_smoke.py`, a Selenium browser test — confirmed by isolated rerun to be a chromedriver/webdriver timeout unrelated to any code this loop touched; see REGRESSION_BASELINE.md.)

**Live verification (real, not mocked) — full read/write lifecycle, twice:**
1. **Direct curl against the real running Functions host + real Cosmos DB**, logged in as `auditadmin`. `GET manage/tokenomics-config` → real defaults. `GET manage/tokenomics/status` → real October usage from the real `llm_observability.jsonl` (`call_count: 131, total_cost_usd: 0.065818`, broken down by 7 real labels), correctly reporting `not_configured` with no budget set. `PUT manage/tokenomics-config` initially 403'd — a REAL, expected recurrence of the same `ensure_default_roles()` insert-only gap found during F-14 (the persisted `admin` role predates `tokenomics.config.write`) — fixed via the real `POST manage/roles/sync` (`{"updated": ["admin"]}`), then succeeded. Saved draft (`$25`/`80%`) → activated → `GET .../history` showed 2 real Cosmos-persisted entries (`draft_saved`, `activated`) → `GET .../status` recomputed to `{"configured": true, "status": "ok", "percent_used": 0.26}`.
2. **Through the actual browser UI** (not curl): logged in via the real frontend+backend, opened AI Observability Center → Budget & Alerts, confirmed it rendered the real `$25/80%` state with no console errors. Typed `$50`/`90%` into the form, clicked Save as Draft (confirmed via network inspection: real `PUT` → 200, draft banner then appeared), clicked Activate Draft (real `POST .../activate` → 200), clicked Refresh, and the page correctly showed "Active: $50/mo, warning at 90%" with `percent_used` recomputed to 0.13%.

**Status:** VERIFIED_WITH_LIMITATIONS. See IMPLEMENTATION_STATUS.md for the full limitations list (no per-user tracking, no cost-saving recommendations, no multi-provider reconciliation beyond existing pricing, no exportable reports — all explicitly deferred, not silently dropped).

---

---

## F-03 — End-to-end candidate feedback system (minimal slice)

**Scope decision:** the full F-03 prompt is a portfolio-scale FDE work order (customizable forms, anonymous/attributable modes, moderation/escalation, department reporting, withdrawal handling). Inspected the real repo first, per the prompt's own Phase A instruction, before designing anything:
- The real "hiring process complete" signal: `ranking_tier` is set to exactly two real terminal values (`Recommended for Fast-Track`/`Rejected`) by `review_candidate_endpoint` and `approve_bulk_decision_endpoint`'s `_apply_bulk_decision` — the only places a human-recorded final decision exists in this app. No separate "offer extended"/"withdrawn" status exists anywhere in `context-lake/schema/candidate.schema.json` or `api_blueprint.py` — confirmed absent, not assumed.
- No "department" field exists anywhere in the real candidate/JD schema — confirmed absent via `candidate.schema.json`/`jd.schema.json` inspection, so department-level filtering is explicitly deferred rather than faked.
- No real candidate auth persona exists in this app (reconfirmed, consistent with the earlier F-08 finding) — so, like `submit_application_endpoint`/`get_candidate_by_email_endpoint`, the candidate-facing feedback endpoints are public, keyed by the opaque `candidate_id` alone.
- Scoped this slice to: an automatic, idempotent feedback-request trigger at the real decision points; a fixed 7-category 1-5 rating form (the categories named in the feature intent); one-way submission; a separate Cosmos container so feedback can never influence `ranking_tier`/`scoring_history`; and a recruiter/admin dashboard with aggregate scores.

**Backend built:**
- `candidate_feedback.py` (new): `FEEDBACK_CATEGORIES` (7 fixed categories), `TERMINAL_RANKING_TIERS`, `is_eligible_for_feedback_request()`, `sanitize_comment()` (control-char strip + length cap — a display-safety cap, not chat_guardrails.py's distinct prompt-injection concern, since this comment is never fed to an LLM), `validate_feedback_submission()`, `summarize_feedback()` (never fabricates a category average with zero real submissions — reports `None`, not `0.0`).
- `context-lake/client.py`: `candidate_feedback` container (`/candidate_id`).
- `context-lake/repositories.py`: `get_feedback_request`, `create_feedback_request` (idempotent — returns the existing doc unchanged if one exists, the real duplicate-prevention mechanism), `submit_feedback` (one-way; raises if no request exists or already submitted), `list_feedback` (optional `jd_id` filter).
- `auth.py`: new `feedback.read` permission, granted to BOTH `admin` and `recruiter` (unlike F-02/F-14's admin-only new permissions) — per the spec's explicit "recruiters and authorized administrators" audience.
- `azure-functions/api_blueprint.py`: `import candidate_feedback`; `_maybe_request_feedback()` helper (best-effort, outside the caller's try/except — same pattern as `_notify_candidate_whatsapp`) wired into both `review_candidate_endpoint` and `_apply_bulk_decision`; 3 new endpoints — `GET`/`POST candidates/{candidate_id}/feedback` (public) and `GET manage/candidate-feedback` (`feedback.read`).

**Frontend built:** per the "extend, don't invent a disconnected page" discipline — added a "Candidate Experience Feedback" section to the existing `RecruiterReports.jsx` (already the recruiter's operational-reports page) rather than a new page, deliberately visually separate from the hiring-evaluation charts above it in the same component. Added `listCandidateFeedback` to `apiClient.js`/`mockApiClient.js` (mock mode uses static illustrative `MOCK_CANDIDATE_FEEDBACK`, since mock mode has no real review-decision flow to auto-create these). Granted `recruiter` role `feedback.read` in the mock permission catalog too. Bumped `frontend/index.html` to `v=56` after all content edits.

**Tests:** `tests/test_candidate_feedback.py` (26, pure-function) + `tests/test_api_blueprint.py` (18 new: `TestMaybeRequestFeedbackHelper` 3, `TestGetCandidateFeedbackRequestEndpoint` 3, `TestSubmitCandidateFeedbackEndpoint` 6, `TestListCandidateFeedbackEndpoint` 6) = 44 new. Also retrofitted 5 pre-existing `TestReviewCandidate`/`TestApproveBulkDecision` tests with a `create_feedback_request` mock — the new auto-trigger made those tests' existing code paths reach a real, previously-unmocked Cosmos call for the first time; caught and fixed before running the full suite, not after a slow/flaky failure (same discipline as the rate-limiter investigation). One test-authoring bug found and fixed in my own new test (`test_golden_jd_id_filter_is_passed_through` used `dict.setdefault(...) or []`, which returns the truthy captured value instead of `[]`) — a test bug, not a feature-code defect.

**Full regression suite:** `python -m pytest tests/ -q --ignore=tests/e2e` → **905 passed, 0 failed, exit 0**, 239.27s (0:03:59). Exact match: 861 + 44 = 905.

**Live verification (real, not mocked) — full lifecycle:**
- Attempted to trigger the real auto-feedback-request path by calling the real `review_candidate_endpoint` against the real demo candidate — **this session's own auto-mode safety classifier correctly blocked it** as a "Real-World Transaction" (that endpoint also sends a real WhatsApp message on every call, which the FDE loop's own safety boundary prohibits without explicit authorization). Did not attempt to work around this; the auto-trigger logic itself remains fully verified via the 5 retrofitted mocked tests.
- Instead, created a real feedback-request document directly via `repositories.create_feedback_request()` for a synthetic, clearly-labeled test candidate id (`test-f03-verification-candidate`) — a safe, reversible, non-consequential write (no audit log, no notification, no real candidate touched).
- Round-tripped against the real running Functions host + real Cosmos DB: `GET .../feedback` → `{"status": "requested", "categories": [...]}` → `POST .../feedback` with real ratings + comment → `{"status": "submitted"}` → retried submission → correctly `409`'d → `GET manage/candidate-feedback` (hit a third real recurrence of the `ensure_default_roles()` sync gap — admin's persisted role predates `feedback.read` — fixed via the same real `POST manage/roles/sync`) → returned the real record with `response_rate: 1.0` and real per-category averages (all 4-5).
- **Through the actual browser UI**: logged in as `auditadmin`, opened Recruiter Reports, and the new "Candidate Experience Feedback" section rendered the exact same real data (1 requested, 1 submitted, 100% response rate, correct per-category averages, the real comment text), with no console errors.

**Status:** VERIFIED_WITH_LIMITATIONS. See IMPLEMENTATION_STATUS.md for the full limitations list (no customizable form builder, no anonymous mode, no moderation/escalation, no department filtering, no withdrawal handling, no exportable reports — all explicitly deferred, not silently dropped).

---

## F-18 — Intelligent LLM caching and response optimization (minimal slice)

**Scope decision:** inspected the full real LLM call-site inventory (`llm_utils.call_with_retry()`'s 7 real labels — chunk summarization, merge extraction, interview-question generation, candidate chat QA, JD generation, RAG groundedness judge, AI match-quality judge) before designing anything. Scoped caching to exactly the two call sites where (a) identical input genuinely recurs in practice (reprocessing the same resume/JD on retry or re-ingestion) and (b) a cached response is only ever written back into that SAME candidate's own Cosmos record, never served to a different user's request — `resume_chunk_summarization` and `resume_merge_structured_extraction` in `ResumeParser.py`. Explicitly did NOT cache candidate chat (conversational, personalized, security-sensitive), interview-question generation (personalized), or the two LLM-as-judge evaluators (caching a judge's score would defeat the point of re-running an evaluation).

**Backend built:**
- `llm_cache.py` (new): exact-match, content-addressed cache. `compute_cache_key()` hashes label+model+full prompt text+temperature+max_tokens+prompt_version — the prompt text already embeds the candidate's own resume/JD content, so cross-candidate leakage is prevented BY CONSTRUCTION (two candidates' resumes produce different keys automatically), and a changed resume/JD self-invalidates the cache (different key) with no separate invalidation step to get wrong. 30-day TTL backstop. Local JSON file storage (`logs/llm_response_cache.json`) — explicitly NOT distributed/shared across instances, documented as a real production-scale gap, not hidden.
- `llm_utils.py`: new `cached_call_with_retry()` wraps the EXISTING `call_with_retry()` rather than duplicating its retry/observability logic — only the two cacheable call sites use it.
- `observability.py`: `record_llm_call()` gained a `cache_hit` param (backward compatible, defaults False) so a cache hit is recorded as a real zero-cost/zero-token event, distinguishable from "a real call that happened to cost $0."
- `tokenomics.py`: `compute_monthly_usage()` gained `cache_hit_call_count` — integrates with the existing tokenomics calculator rather than a parallel cache-analytics system, per the feature's own explicit requirement.
- `ResumeParser.py`: the two real call sites switched from `call_with_retry` to `cached_call_with_retry` (same args, added `temperature=`/`max_tokens=` which weren't previously threaded through since they weren't needed for the old call).
- `auth.py`: new `llm_cache.manage` permission (admin-only, destructive clear action); viewing reuses `analytics.read`.
- `azure-functions/api_blueprint.py`: `GET manage/llm-cache/stats` (real stats + an ESTIMATED savings figure computed from this month's real historical per-label cost data, explicitly labeled an estimate, `null` when no real data exists to estimate from) and `POST manage/llm-cache/clear` (audited, best-effort — a logging failure never blocks the real clear).

**Frontend built:** new "Response Cache" tab on the existing AI Observability Center (`LlmCachePanel`) — same "extend, don't invent a disconnected page" discipline as F-02's Budget & Alerts tab. `apiClient.js`/`mockApiClient.js` methods added; mock mode uses clearly-labeled illustrative stats (mock mode never calls a real LLM, so there's no real cache to report on).

**Tests:** `tests/test_llm_cache.py` (20, pure-function cache store), `tests/test_llm_utils.py::TestCachedCallWithRetry` (5, the wrapper), `tests/test_api_blueprint.py` (8 new: `TestGetLlmCacheStatsEndpoint` 4, `TestClearLlmCacheEndpoint` 4) = 33 new.

**Full regression suite:** `python -m pytest tests/ -q --ignore=tests/e2e` → **938 passed, 0 failed, exit 0**, 238.64s (0:03:58). Exact match: 905 + 33 = 938.

**Live verification (real, not mocked) — full lifecycle against the real running Functions host:**
- `GET manage/llm-cache/stats` on a genuinely empty cache correctly reported `entry_count: 0` and `estimated_savings_usd_this_month: null` (never a fabricated $0 "savings" with nothing cached).
- Hit the known `ensure_default_roles()` sync gap a fourth time (same recurring, expected finding as F-02/F-03/F-14) — fixed via `POST manage/roles/sync`.
- Wrote one real cache entry directly via `llm_cache.set()` (a safe, non-consequential local-file write — deliberately NOT by running the actual resume-ingestion pipeline, which wasn't necessary to exercise the real cache store/API layer and avoids any real OpenAI spend).
- `GET .../stats` correctly reported it with a real estimated-savings figure computed from real historical cost data for that label.
- `POST .../clear` removed it (`{"status": "cleared", "removed_count": 1}`); a follow-up `GET .../stats` confirmed the cache was genuinely empty afterward.
- The new "Response Cache" tab was confirmed rendering correctly in the actual browser against the real host (screenshot captured, shown to the user).
- Did not live-exercise the real `ResumeParser.py` cache-HIT code path itself (would require running the actual resume-ingestion pipeline against a real file and real OpenAI spend) — that logic is covered by the 5 mocked `TestCachedCallWithRetry` tests instead, consistent with this repo's existing precedent that `ResumeParser.py` has no dedicated test file and is verified live/manually for its own pipeline behavior.

**Status:** VERIFIED_WITH_LIMITATIONS. See IMPLEMENTATION_STATUS.md.

---

## Out-of-band: Azure Observability Report real-data-erasure bug (direct user report)

**Not one of the 26 numbered FDE features** — the user reported, with screenshots, that the Service Observability Report's charts showed "Unavailable" with stale error text baked in, and asked that real Azure API outputs be stored durably so the dashboard doesn't need to re-fetch on every open, with the last-refresh time shown, and the charts actually working. Addressed immediately as a direct, explicit user request (standing priority over the autonomous feature loop per this loop's own instructions).

**Investigation:** read `observability_snapshots.py` and `AzureObservabilityReport.jsx` in full before touching anything. Found the persist/serve-from-snapshot/never-re-query-on-open/show-last-refresh-time architecture the user was describing ALREADY EXISTED (built earlier this engagement) and was working correctly at the mechanism level. The actual bug was more specific: `azure_monitor_query.py`'s functions (`get_request_volume`, `get_latency_percentiles`, `get_ai_usage`, `get_recent_errors`) never raise on a Log Analytics query failure — by their own documented design, they catch it and return `{"available": False, "reason": ..., "detail": "..."}` normally. `observability_snapshots.run_refresh()`'s merge logic only engaged its `last_known_good` fallback when a source's fetch_fn raised an actual exception (`status == "failed"`) — a source that returned normally with `available: False` inside its payload was merged as a genuine `"status": "success"`, silently overwriting (and permanently erasing) whatever real data a previous successful refresh had captured for that source, with no fallback ever engaging. Confirmed this exact bug live against the real persisted Cosmos snapshot: 4 sources showed `status: "success"` with `available: false` buried inside their `data`.

**Fix:** added `_is_soft_unavailable()` to `observability_snapshots.py` — detects a `"success"`-status result whose own `data` is a dict with `"available": False` and treats it like a real failure for snapshot-merging purposes (so `last_known_good` correctly engages and is preserved), while still logging the real round-trip outcome to the per-source refresh-result history. `get_telemetry_health()`'s different shape (no `"available"` key) is correctly NOT misclassified — verified by a dedicated test.

**Tests:** `tests/test_observability_snapshots.py` (new, 11 tests) — covers the detection function directly, and `run_refresh()`'s merge behavior: first-ever soft-unavailable has no fallback, soft-unavailable correctly preserves a prior real success, a genuine new success replaces both live data and any stale fallback, a real exception still works as before, `not_configured` is never misclassified as soft-unavailable, and `get_telemetry_health`'s shape is never misclassified.

**Live verification (real, not mocked):**
- Before the fix: confirmed via the real persisted snapshot that `azure_request_volume`/`azure_latency`/`azure_ai_usage`/`azure_recent_errors` all showed `status: "success"` with `available: false` buried inside — the exact bug.
- Restarted the Functions host to load the fix (root-level `.py` module changes aren't picked up by `func start`'s file watcher the way `azure-functions/`-local files are — closed and reopened the terminal tab running it).
- Triggered a real, scoped refresh (`POST manage/observability/refresh`, `source_ids: ["azure_request_volume"]`) against the real (still-failing, expired-TAP) Log Analytics endpoint: confirmed the result correctly flipped to `status: "failed"` with a real error message and (correctly, since no genuine prior success exists in this environment's history) `last_known_good: null`.
- Confirmed through the actual browser UI: the banner now honestly reports "0 succeeded, 1 failed" (previously falsely claimed success), and the KPI tiles now show "(showing last known good data from an earlier refresh)" where a fallback is available — the fallback mechanism visibly engaging for the first time.
- **Did not fabricate a "fixed" demo**: this environment's Azure CLI session has a genuinely expired Temporary Access Pass (same sandbox-credential issue documented repeatedly elsewhere in this engagement), so the charts still show "Unavailable" right now — correctly and honestly, not falsely. The fix's real value (preserving genuine data across a later failure) will be demonstrated the next time a real refresh actually succeeds; told the user this plainly rather than claiming more than was shown.

**Full regression suite:** run after this fix (same run that also covers F-18, which landed just before this bugfix) — see REGRESSION_BASELINE.md for the exact count once confirmed.

**Status:** Fixed and live-verified (the fix's mechanism, not a "populated charts" outcome, which depends on Azure CLI auth outside this session's control).

---

---

## F-17 — Intelligent AI model routing and optimization (minimal slice)

**Scope decision:** inspected every real LLM call site in the repo before designing anything (the same 7-label inventory established during F-18). Found that `resume_chunk_summarization`/`resume_merge_structured_extraction` already have a real admin-configurable model field via `rag_config.py`'s RAG Configuration Console — deliberately did NOT duplicate that. Found 6 other real, live call sites with the model hardcoded to `"gpt-3.5-turbo"` with zero admin control: `chat_candidate_qa` and `interview_questions_generation` in `azure-functions/api_blueprint.py`, `rag_groundedness_judge` and `ai_match_quality_judge` (also in `api_blueprint.py`), `jd_generation` and `jd_document_extraction` in `jd_management.py`, and `interview_bot_answer_scoring` in `interview_bot.py`. Scoped this slice to exactly that real gap: a centralized override registry for those 6/7 labels, with today's exact hardcoded values preserved as the default (zero regression when unconfigured). Confirmed via import-graph inspection that `interview_bot.py` is a standalone CLI script (builds its LLM client at module-import time, uses `input()`) with no live HTTP call site anywhere in the repo — included its label in the registry for forward-compatibility but did not restructure that module's import-time side effect to wire it live, since the real value here is the 5 genuinely live-traffic call sites.

**Backend built:**
- `model_routing.py` (new): `ROUTABLE_LABELS` (7 labels), `DEFAULT_MODELS` (today's real hardcoded value for each), `validate_routing_config()`, `resolve_model(label, config)` → `(model, source)`, `get_runtime_routing_config()` (mirrors `rag_config.get_runtime_config()`'s exact try/except-fall-back-to-empty discipline).
- `context-lake/client.py`: `model_routing_config`/`model_routing_config_history` containers — a single "active" document, deliberately NOT the Draft→Activate staging pattern used for rag_config/comms_config/tokenomics — a routing-rule change is low-risk and instantly reversible by resaving, documented as a deliberate simplification.
- `context-lake/repositories.py`: `get_model_routing_config`, `save_model_routing_config` (whole-replace + history write, same semantics as `set_pipeline_config`), `list_model_routing_config_history`.
- `auth.py`: new `model_routing.manage` permission (admin-only write); viewing reuses `analytics.read`.
- `azure-functions/api_blueprint.py`: `GET`/`PUT manage/model-routing-config`, `GET manage/model-routing-config/history`; the 4 live call sites in this file (`chat_candidate_qa`, `interview_questions_generation`, `rag_groundedness_judge`, `ai_match_quality_judge`) each now call `model_routing.resolve_model(label, model_routing.get_runtime_routing_config())` before constructing their `get_chat_llm()` client, instead of the hardcoded string.
- `jd_management.py`: same pattern applied to `jd_document_extraction` and `jd_generation`.
- `conftest.py`: added `repositories.get_model_routing_config` to the repo-wide autouse fixture (defaulting to `None` → every label falls back to its real default) — this new per-call Cosmos lookup is reached by roughly 22 pre-existing tests across the chat/interview-question/judge endpoint test classes that never mocked it before this feature existed. Added BEFORE running the full suite, not discovered via a slow/flaky failure — same proactive discipline the rate-limiter investigation and the F-03 auto-trigger retrofit both established.

**Frontend built:** new "Model Routing" tab on the existing AI Observability Center (`ModelRoutingPanel`) — a table of all 7 routable labels with an editable override field and each one's real default shown alongside, Save button, last-updated-by line. `apiClient.js`/`mockApiClient.js` methods added; mock mode mirrors the real `ROUTABLE_LABELS`/`DEFAULT_MODELS` exactly (not a second invented list), and is clearly a config-only surface since mock mode never calls a real model.

**Tests:** `tests/test_model_routing.py` (19, pure-function: validation, resolution including a test that every `DEFAULT_MODELS` entry matches the real hardcoded value it replaced, runtime-config fallback) + `tests/test_api_blueprint.py` (12 new: `TestGetModelRoutingConfigEndpoint` 4, `TestSaveModelRoutingConfigEndpoint` 6, `TestGetModelRoutingConfigHistoryEndpoint` 2) = 31 new.

**Full regression suite:** `python -m pytest tests/ -q --ignore=tests/e2e` → **980 passed, 0 failed, exit 0**, 235.53s (0:03:55). Exact match: 949 + 31 = 980. The 22 pre-existing tests reached by the new `get_runtime_routing_config()` call (`TestChatCandidateQa`/`TestGenerateInterviewQuestions`/`TestGroundednessEvaluationHelper`/`TestMatchQualityEvaluationHelper`) all re-ran clean with the `conftest.py` fix in place, confirmed via a targeted `-k` run before the full suite.

**Live verification (real, not mocked) — full lifecycle against the real running Functions host + real Cosmos DB:**
- `GET manage/model-routing-config` returned real defaults with `routes: {}`.
- `PUT` initially 403'd — the known `ensure_default_roles()` sync gap, its fifth real recurrence this engagement (F-02/F-03/F-14/F-18/F-17) — fixed via the same real `POST manage/roles/sync`.
- Saved a real override (`chat_candidate_qa` → `gpt-4o-mini`), confirmed reflected in a follow-up `GET` and in `GET .../history` (1 real Cosmos-persisted entry with the real actor and timestamp).
- **Deliberately reset the override back to `{}` immediately after verification** — `gpt-4o-mini` is not a real Azure deployment actually provisioned in this sandbox, and leaving it active would have silently broken the real `chat_candidate_qa` endpoint (routing it to a model that doesn't exist) for any genuine subsequent use. This is the same "don't leave the demo environment in a worse state than you found it" discipline as F-02/F-03's test-data choices.
- **Through the actual browser UI**: confirmed the new "Model Routing" tab renders the real post-reset state correctly (all 7 labels, their real `gpt-3.5-turbo` defaults, the real `Last updated by auditadmin` line), with no new console errors — one pre-existing, unrelated 404 was observed but confirmed via network-request inspection to not involve any endpoint this feature touches.

**Status:** VERIFIED_WITH_LIMITATIONS. See IMPLEMENTATION_STATUS.md for the full limitations list (not a routing engine — no multi-provider comparison, automatic complexity-based selection, quality-triggered escalation, or spending limits; this environment has only one real model/provider configuration to select, not multiple to route between; `interview_bot_answer_scoring` registered but not live-wired).

---

---

## F-11 — Comprehensive application performance and load testing (minimal slice)

**Scope decision:** this environment runs as a single local `func start` process (confirmed earlier this engagement, `observability_snapshots.py`'s own documented scope), not a scaled cloud deployment — "stress/spike/scalability at production concurrency" is not an honest claim this environment can produce real evidence for. Scoped to a real, safe LOAD-testing harness only, with a hard-coded safety allowlist of endpoints that cross-referenced against every `observability.record_llm_call()` call site to confirm none of them trigger a real, billable AI call — the feature's own explicit "do not incur unexpected cloud or API costs" requirement, enforced structurally (an allowlist), not by convention. Followed F-12's existing precedent (the Selenium E2E smoke test) that engineering/test tooling doesn't need an API endpoint, permission, or frontend UI — this is operational tooling for engineering, not a product capability end users configure.

**Built:**
- `perf_testing.py` (new): `SAFE_SCENARIOS` (health, candidates_list, pipeline_config, observability_summary — all read-only, non-AI), `DEFAULT_MAX_CONCURRENCY`/`DEFAULT_MAX_REQUESTS` hard caps (enforced even if a caller asks for more), `compute_percentiles()` (nearest-rank, real empty-sample handling), `summarize_run()` (real throughput from real wall-clock span, percentiles over succeeded requests only), `run_load_scenario()` (real `ThreadPoolExecutor`-based concurrent HTTP calls via stdlib `urllib.request` — no new dependency added).
- `tests/perf/test_load_baseline.py` (new): a real, runnable load-test baseline against the real local Functions host — `pytest.skip()` (BLOCKED, not FAIL) if nothing is listening on port 7071, consistent with the E2E test's own "environmental prerequisite failure is not the same as the application being broken" discipline. When the host IS running, writes a real report to `docs/fde/PERFORMANCE_BASELINE.md`.

**Tests:** `tests/test_perf_testing.py` (16, pure-function + mocked orchestration: percentile/summary math, scenario allowlist/auth enforcement, concurrency/request-count hard-cap enforcement) + `tests/perf/test_load_baseline.py` (2, real live tests) = 18 new.

**Real live run (not mocked, against the actual running Functions host):** `pytest tests/perf/test_load_baseline.py -v -s` → **2 passed in 31.09s**. Real measured results, written to `docs/fde/PERFORMANCE_BASELINE.md`:

| Scenario | Requests | Concurrency | Failures | p50 | p95 | p99 | Throughput |
|---|---|---|---|---|---|---|---|
| health | 30 | 10 | 0 | 6.3ms | 26.2ms | 26.7ms | 727 req/s |
| candidates_list | 20 | 5 | 0 | 2407.8ms | 3363.1ms | 3371.0ms | 1.87 req/s |
| pipeline_config | 20 | 5 | 0 | 2526.0ms | 2596.0ms | 2613.3ms | 1.98 req/s |
| observability_summary | 20 | 5 | 0 | 1827.8ms | 1867.8ms | 1898.6ms | 2.72 req/s |

**Real finding, investigated to root cause (not just reported as a number):** every authenticated endpoint is 100-400x slower than the unauthenticated health check. Traced it: `secrets_loader.get_secret()` has zero caching on its Key Vault lookup result (confirmed by reading the module — no memoization anywhere in its path), and `auth.py`'s `_jwt_secret()` calls `get_secret("JWT_SECRET")` fresh on EVERY single `require_permission()` call, which every authenticated endpoint makes. In this environment the Azure CLI credential in `DefaultAzureCredential`'s chain is failing (the same documented expired-Temporary-Access-Pass issue that's recurred throughout this engagement), so every authenticated request pays the full credential-chain-exhaustion cost (~2s) before falling back to `.env`. This is a genuine, structural performance bug, not an artifact of this environment's Azure outage alone — it would still cost real latency (just less) even with a healthy Key Vault connection, since there's still no caching.

**Deliberately NOT fixed in this slice** — out of scope for a load-testing feature (fixing a cross-cutting secrets-caching bug is a distinct, separately-scoped engineering task, and this loop's own discipline is not to silently expand scope). Flagged via `spawn_task` (`task_caf06b0d`, "Cache get_secret() per-process to cut ~2s/request latency") with the exact root cause, a concrete fix approach, and an explicit before/after verification plan (re-run this same load-test baseline and compare against the numbers already captured above) — a real, actionable recommendation, not a vague "optimize performance" note.

**Status:** VERIFIED_WITH_LIMITATIONS. See IMPLEMENTATION_STATUS.md.

---

---

## F-07/F-09 — Candidate chatbot (minimal slice: frontend for the existing F-08 backend)

**Scope decision:** checked `docs/fde/IMPLEMENTATION_STATUS.md` first and found this real gap already documented from earlier this engagement: Phase 1 Task 4 built F-08's real, backend-enforced, permission-gated chat endpoint (`chat_guardrails.py`, `POST manage/chat/candidate-qa`) with the limitation explicitly noted — "no frontend chat UI (deferred)." F-07 (preliminary interview chatbot) and F-09 (pre/post-selection candidate support chatbots) both describe candidate-facing chat experiences, but this app has no real candidate authentication persona (confirmed finding, unchanged) — so, consistent with F-08's own documented scoping decision, the real, honest minimal slice for both is the missing STAFF-facing frontend for the chat capability that already exists and is already tested, not a candidate-facing rebuild that would require inventing candidate auth first.

**Built:** `apiClient.js`/`mockApiClient.js` `chatCandidateQa()` (mock mode honestly rejects, same discipline as `generateInterviewQuestions`/`generateJdWithAi`). New "Ask about this candidate" section in `StageTimelineModal.jsx` (the existing candidate-detail popout, right after the Interview Questions section it already has) — a simple question/answer chat history (session-only, matching the Interview Questions section's own "not saved automatically" convention), gated by the existing `candidates.read` permission. `styles.css`: `.candidate-chat-*` classes.

**Zero backend changes** — the real endpoint (`chat_candidate_qa_endpoint`) was fully built and tested in an earlier phase of this engagement; this slice is purely the missing UI layer calling it. No new tests needed (the endpoint's own existing test coverage — auth, 404, error handling — still fully applies; nothing about its contract changed).

**Live verification (real, not mocked) — through the actual browser UI against the real running Functions host:** opened the real demo candidate's Stage Timeline modal from the Pipeline view (Reviewed column), typed "What is this candidate's strongest technical skill?" into the new chat input, and received a real, grounded LLM answer: *"The candidate's strongest technical skill is Python programming, as highlighted by their strong academic and internship background, relevant project experience, and alignment with the job description."* — correctly reflecting the real candidate's actual resume profile (matches their real recorded skills). Rendered correctly in the new chat-history UI with no new console errors (confirmed the one observed 404 is the same pre-existing, unrelated issue already investigated during F-17's verification).

**Full regression suite:** unchanged from F-11's 998 (no backend code touched, no new tests added) — re-ran anyway to confirm, per this loop's own "verify after every feature" discipline: see REGRESSION_BASELINE.md.

**Status:** VERIFIED_WITH_LIMITATIONS. See IMPLEMENTATION_STATUS.md.

---

---

## Out-of-band: real resume-parsing pipeline bugs found during a live multi-persona demo (direct user reports)

**Not one of the 26 numbered FDE features.** While walking through a real, live, multi-persona demo (5+ real candidate accounts applying via the real "Apply" form in an actual browser, a real recruiter reviewing them, real admin verification), the user directly reported — and this session investigated and fixed — three real, previously-undetected bugs in `ResumeParser.py`'s real pipeline, surfaced only by real, concurrent, multi-candidate traffic (not something any single-candidate test run had ever exercised).

**Bug 1 — "Not Provided" literal LLM output slipped past the name-fallback check.** `ResumeParser.py`'s fallback-trigger condition (`not gpt_name or "candidate" in gpt_name.lower() or gpt_name.startswith("<")`) never caught a real, observed LLM output: the model literally answering `"Not Provided"` for a resume's Name field. That string is non-empty, doesn't mention "candidate", and doesn't start with "<", so it sailed through unflagged into the real Cosmos record. **Fix:** new `is_placeholder_name()` helper in `resume_text_utils.py` (checks a real, confirmed set of placeholder phrases — "not provided", "n/a", "unknown", "none", etc. — case/punctuation-insensitive), wired into the existing fallback condition. **Tests:** 8 new (`TestIsPlaceholderName` in `tests/test_resume_text_utils.py`).

**Bug 2 — a candidate's real `ranking_tier` depended on a separate, fragile post-processing step.** `ResumeParser.py`'s own `upsert_candidate()` call never set `ranking_tier` at all — that only happened via a SEPARATE step (`function_app.py`'s `resume_blob_trigger` → `_apply_realtime_tier`), which (a) never runs at all for this script's own standalone/CLI/batch invocation path, and (b) is fragile under the blob trigger's own documented concurrent-upload race condition for the real async path. Real, observed consequence: 8 of 13 real candidates processed during this session's live demo were stuck with `ranking_tier: None` indefinitely — no recommendation at all, not even a wrong one. **Fix:** `ResumeParser.py` now calls `compute_realtime_tier()` directly inside its own per-resume write, so every candidate gets a real tier the moment their record is first written, regardless of which code path wrote it.

**Bug 3 — one file's move failure crashed the entire batch run, silently blocking every resume still queued behind it.** The `shutil.move(...)` call moving a processed PDF into `ParsedReadyForQuestions/` was NOT wrapped in its own error handling. Real, observed failure: a concurrent run (same documented race condition as Bug 2) had already moved `elena_petrova_resume.pdf` a moment earlier; the next run's `shutil.move` for that same file raised `FileNotFoundError`, which propagated all the way out of the script — aborting processing for every OTHER resume still left in that run's loop (including two real candidates, Jordan Lee and Taylor Brooks, who were never even attempted). **Fix:** scoped that failure to just the one file (`try`/`except (FileNotFoundError, shutil.Error)`, logged and continues).

**Real backfill applied (no new LLM calls, no fabricated data):** for the 8 already-stuck candidates, directly computed and wrote their real `ranking_tier` from their ALREADY real-extracted `JD Match`/`Resume Strength Score` fields (same `compute_realtime_tier()` function the real pipeline uses) — a deterministic, zero-cost recomputation, not a guess. Also backfilled 10 candidates' real `Name` fields from already-known real submission data (no invented names).

**Live verification (real, not mocked):** after all three fixes, submitted two new real applications deliberately chosen to be a borderline fit (Jordan Lee — IT support background, no Python/AI experience) and a clear mismatch (Taylor Brooks — marketing background, no technical skills at all) through the real public Apply API. The real pipeline, with all three fixes applied, correctly processed both end to end with NO crash this time: **Jordan Lee → real `JD Match: 65%`, `Resume Strength Score: 6.5` → `ranking_tier: "To be Considered"`**; **Taylor Brooks → real `JD Match: 10%`, `Resume Strength Score: 2.0` → `ranking_tier: "Rejected"`** — genuine AI scoring producing a real spread across all three tiers (previously every single real candidate had landed in "Recommended for Fast-Track" only, which was itself a symptom nobody had investigated until this session's live, concurrent-traffic demo surfaced it). Confirmed in the real browser UI: Recruiter Dashboard now shows `11 Fast-Track / 1 To Be Considered / 1 Rejected`.

**Full regression suite:** `python -m pytest tests/ -q --ignore=tests/e2e --ignore=tests/perf` → **1004 passed, 0 failed, exit 0**, 240.95s (0:04:00), run twice across these fixes (once after Bugs 1+2, once after Bug 3), both green.

---

## Out-of-band: tier labels reworded to read as an AI suggestion, not a command (direct user report)

The user explicitly asked that tier labels — especially "Rejected" — read as an AI *recommendation* a human reviewer can accept or override, not a decision already carried out. **Fix (presentation-only, zero data/behavior change):** new `frontend/src/uiLabels.js` (`tierDisplayLabel()`, a `ranking_tier` → friendlier-text map: `"Recommended for Fast-Track"` → `"AI Suggests: Fast-Track"`, `"To be Considered"` → `"AI Suggests: Consider Further"`, `"Rejected"` → `"AI Suggests: Not a Match"`), applied everywhere a tier is rendered as read-only text for a human (`Dashboard.jsx`'s `TierBadge` and tier-filter `<select>`, `ReviewQueue.jsx`'s tier-filter `<select>`, `Pipeline.jsx`'s Kanban card detail text, `AdminReports.jsx`'s Tier Distribution donut legend and the Fast-Track-vs-Rejected radar chart's dataset labels). Deliberately NOT applied to: any `<option value=...>` VALUE, any tier-filter API query parameter, `ReviewQueue.jsx`'s `handleDecision()` calls (`"approve"`/`"reject"` are real actions, not display text), or `RecruiterReports.jsx`'s "Individual Review Decisions" chart (`"Approved"`/`"Rejected"` there describe a REAL human decision already made, correctly left as plain past-tense text, not softened). The real stored/filtered/API `ranking_tier` value is completely unchanged everywhere it's used as data. No backend tests affected (pure frontend presentation change).

---

*(Next entry: selecting the next eligible feature per IMPLEMENTATION_BACKLOG.md, if/when the user returns to the autonomous loop.)*
