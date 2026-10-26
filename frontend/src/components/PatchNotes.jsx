/**
 * Patch Notes: a hand-maintained, honest record of what has actually
 * been built in CDPI and what hasn't yet -- not a live-generated report.
 * Same discipline as this repo's own docs/fde/IMPLEMENTATION_STATUS.md
 * and docs/fde/IMPLEMENTATION_BACKLOG.md (the source of truth this page
 * summarizes for a non-technical reader): a status here is only ever
 * "Shipped" if real code + real tests + real verification exist for it,
 * and anything not yet built says so plainly rather than being omitted.
 *
 * Status vocabulary mirrors the FDE engineering loop's own:
 *   shipped              -- real, tested, verified (may still have
 *                            documented limitations -- see each entry).
 *   in_progress           -- actively being built this session.
 *   planned               -- identified, not started yet.
 *   blocked               -- needs a decision or dependency first.
 *
 * This file is the single place to add a new entry when something ships
 * or a new gap is identified -- update it by hand alongside the actual
 * change, the same moment docs/fde/IMPLEMENTATION_STATUS.md is updated.
 */
var PATCH_STATUS_LABEL = { shipped: "Shipped", in_progress: "In progress", planned: "Planned", blocked: "Blocked" };
var PATCH_STATUS_BADGE_CLASS = { shipped: "badge-good", in_progress: "badge-neutral", planned: "badge-neutral", blocked: "badge-bad" };

var SHIPPED_FEATURES = [
  { id: "IAM", title: "Real authentication + RBAC", status: "shipped",
    note: "Real login (bcrypt + JWT), permission catalog, 12 persona-mapped roles, backend-enforced on every protected endpoint." },
  { id: "PAGE-ACCESS", title: "Dynamic Page Access Management", status: "shipped",
    note: "Per-role, per-page access policies, admin-configurable, deny-by-default for newly added pages (including this one)." },
  { id: "AZURE-OBS", title: "Azure Observability & Readiness suite", status: "shipped",
    note: "Real Azure Monitor/Log Analytics query-back, snapshot persistence (refresh-on-demand, never silently re-queries), Azure Service Hub, RAG & Communications configuration consoles, Secrets Management." },
  { id: "PIPELINE", title: "Candidate pipeline governance", status: "shipped",
    note: "No-skip stage enforcement, required notes/performance on every stage advance, bulk-select propose/approve workflow with a separate admin-approval gate." },
  { id: "JD-MGMT", title: "Job Description Management", status: "shipped",
    note: "Full draft -> submit -> approve -> publish -> archive lifecycle, Cosmos-backed, bridges into the real resume-scoring pipeline only on publish." },
  { id: "F-10", title: "Prompt-injection defense + detection logging", status: "shipped",
    note: "Resume text and interview answers sanitized before every LLM call (generate_questions.py, interview_bot.py); every flagged attempt is also logged as a queryable security event, automatically, for every call site." },
  { id: "F-08", title: "Backend-enforced AI chat guardrails", status: "shipped",
    note: "Staff-facing candidate Q&A chat -- authorization resolved server-side before any prompt is built, never by the model. No frontend chat UI yet (see Planned)." },
  { id: "F-06", title: "AI-grounded interview question generation", status: "shipped",
    note: "Generates real interview questions grounded in both a candidate's resume profile and a chosen job description -- live-verified end to end. Visible in Pipeline -> candidate -> Interview questions." },
  { id: "F-16", title: "Minimal fairness guard", status: "shipped",
    note: "Static check that no protected-attribute-shaped field can reach resume scoring or the candidate schema. Not a fairness certification -- see Blocked for the full measurement scope." },
  { id: "F-19", title: "RAG evaluation framework (minimal slice)", status: "shipped",
    note: "Real golden-question dataset verified against the live semantic index, retrieval hit-rate evaluation, one LLM-as-judge groundedness check. Live result: 3/4 hit rate, with one honestly-documented scoring-ceiling limitation. No frontend UI yet (see Planned)." },
  { id: "F-21", title: "AI evaluation / QA framework (minimal slice)", status: "in_progress",
    note: "A real structured-output quality gate on the resume-scoring extraction contract, plus one LLM-as-judge candidate-job match-quality check, reusing F-19's judge infrastructure directly. Backend in progress this session." },
];

var PLANNED_FEATURES = [
  { id: "F-08-UI", title: "Chat guardrails: frontend UI", status: "planned",
    note: "F-08's backend is real and tested; no chat widget is wired into the frontend yet." },
  { id: "F-19-UI", title: "RAG evaluation: frontend UI", status: "planned",
    note: "RAG Configuration already has a Preview & Evaluation tab -- the natural place to surface F-19's results; not wired yet." },
  { id: "F-20", title: "Failure safety / idempotency (narrow slice)", status: "blocked",
    note: "The natural first target (duplicate-email prevention) has no real call site yet -- needs F-05 (candidate emails) first, or a different real target." },
  { id: "F-14", title: "Audit evidence & candidate data erasure", status: "planned",
    note: "Audit logging is real; a Cosmos-scoped erasure workflow is not built yet." },
  { id: "F-02", title: "Tokenomics calculator UI", status: "planned",
    note: "Cost/token tracking is real (AI Observability Center); a dedicated budget/alert UI is not built yet." },
  { id: "F-05", title: "AI shortlisting sanity-checker + candidate email", status: "planned",
    note: "Needs a recruiter-approval gate designed before any candidate email can be sent automatically." },
  { id: "F-04", title: "Interviewer-candidate matching", status: "blocked",
    note: "Depends on a fairness-measurement-scope decision (see F-16) before it's safe to build." },
  { id: "F-13", title: "AI governance / GDPR / EU AI Act", status: "blocked",
    note: "Legal-interpretation task requiring a named jurisdictional scope decision -- never a code-only \"compliance achieved\" claim." },
  { id: "F-25", title: "Local vs. frontier model benchmarking", status: "blocked",
    note: "Needs a decision on whether local/self-hosted model infrastructure is actually in budget." },
  { id: "F-24", title: "Repository architecture visualization", status: "blocked",
    note: "The proposed tool's real capabilities for this stack have never been verified." },
  { id: "F-23", title: "Documentation generation center", status: "planned",
    note: "The existing docs/ tree already serves this need; revisit only if a real need arises." },
  { id: "F-03, F-07, F-09, F-11, F-17, F-18, F-22", title: "Remaining backlog (feedback, chatbots, load testing, model routing/caching, AIOps)", status: "planned",
    note: "Not started -- lower priority than the items above in the current engineering loop." },
];

function PatchNoteRow(props) {
  var item = props.item;
  return (
    <div className="patch-note-row">
      <div className="patch-note-row-header">
        <span className="patch-note-id">{item.id}</span>
        <span className="patch-note-title">{item.title}</span>
        <span className={"badge " + PATCH_STATUS_BADGE_CLASS[item.status]}>{PATCH_STATUS_LABEL[item.status]}</span>
      </div>
      <p className="muted patch-note-text">{item.note}</p>
    </div>
  );
}

function PatchNotes() {
  return (
    <div className="view">
      <h2>Patch Notes</h2>
      <p className="muted">
        A hand-maintained, honest record of what's actually been built in CDPI and what hasn't yet --
        kept in sync with <code>docs/fde/IMPLEMENTATION_STATUS.md</code> and{" "}
        <code>docs/fde/IMPLEMENTATION_BACKLOG.md</code>. A feature only appears as "Shipped" once it has
        real code, real tests, and real verification -- not because a page or endpoint exists for it.
      </p>

      <h3>Shipped</h3>
      <div className="patch-note-list">
        {SHIPPED_FEATURES.map(function (item) { return <PatchNoteRow key={item.id} item={item} />; })}
      </div>

      <h3>Planned / not yet added</h3>
      <div className="patch-note-list">
        {PLANNED_FEATURES.map(function (item) { return <PatchNoteRow key={item.id} item={item} />; })}
      </div>
    </div>
  );
}
