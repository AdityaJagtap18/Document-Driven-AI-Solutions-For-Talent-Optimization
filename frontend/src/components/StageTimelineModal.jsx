/**
 * Popout shown when a recruiter/admin clicks a candidate: their hiring-
 * process timeline against the recruiter-configured stage list (see
 * repositories.get_pipeline_config() / PipelineConfigModal.jsx) — a
 * vertical stepper, reached stages filled in with who recorded it and
 * when, plus (when editable) a control to record the next stage.
 *
 * Not the same thing as the Kanban Pipeline view's 4 fixed columns
 * (Applied/Scored/Document Verified/Reviewed) — those are system-derived
 * milestones; this is the recruiter's own configurable hiring-round
 * process (exams, interview rounds, etc), tracked per candidate as
 * stage_update audit_log entries.
 *
 * `editable` controls whether the "record a stage" form renders — the
 * Candidate Portal reuses the same stepper markup read-only (via its own
 * inline rendering, see CandidatePortal.jsx) rather than this modal,
 * since a candidate's own timeline is shown inline on their one page,
 * not as a popout.
 *
 * NO SKIPPING: the form never shows a free choice of stage -- it computes
 * the single valid next stage (first configured stage if nothing's
 * recorded yet, otherwise whatever immediately follows the current one)
 * and that's the only thing that can be recorded, matching exactly what
 * advance_candidate_stage_endpoint enforces server-side. Notes are
 * required (not just a nice-to-have): the point of recording a stage is
 * capturing how the candidate did there, not just that they showed up.
 */
var PERFORMANCE_RATINGS = ["Excellent", "Good", "Average", "Poor"];

function StageTimelineModal(props) {
  var api = props.api;
  var candidateId = props.candidateId;
  var candidateName = props.candidateName;
  var onClose = props.onClose;

  var state = React.useState({ loading: true, error: null, timeline: [], stages: [] });
  var data = state[0];
  var setData = state[1];
  var formState = React.useState({ updatedBy: "", notes: "", performance: "" });
  var form = formState[0];
  var setForm = formState[1];
  var submitState = React.useState({ submitting: false, error: null });
  var submit = submitState[0];
  var setSubmit = submitState[1];

  function load() {
    return Promise.all([api.getCandidateTimeline(candidateId), api.getPipelineConfig()])
      .then(function (results) {
        setData({ loading: false, error: null, timeline: results[0].timeline, stages: results[1].stages });
      })
      .catch(function (err) {
        setData(function (d) { return Object.assign({}, d, { loading: false, error: err.message }); });
      });
  }

  React.useEffect(function () {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candidateId]);

  var reachedStages = data.timeline.map(function (t) { return t.stage; });
  var currentStage = data.timeline.length ? data.timeline[data.timeline.length - 1].stage : null;

  // The ONE valid next stage, same computation advance_candidate_stage_endpoint
  // does server-side -- the form never offers a free choice.
  var nextStage = null;
  var blockedReason = null;
  if (data.stages.length > 0) {
    if (currentStage === null) {
      nextStage = data.stages[0];
    } else {
      var idx = data.stages.indexOf(currentStage);
      if (idx === -1) {
        blockedReason = "Current stage '" + currentStage + "' is no longer in the configured pipeline. Fix the stage list before advancing this candidate.";
      } else if (idx === data.stages.length - 1) {
        blockedReason = "'" + currentStage + "' is already the final configured stage — nothing left to advance to.";
      } else {
        nextStage = data.stages[idx + 1];
      }
    }
  }

  function handleAdvance(e) {
    e.preventDefault();
    if (!nextStage || !form.updatedBy.trim() || !form.notes.trim()) return;
    setSubmit({ submitting: true, error: null });
    api.advanceCandidateStage(candidateId, nextStage, form.updatedBy.trim(), form.notes.trim(), form.performance || undefined)
      .then(function () {
        setSubmit({ submitting: false, error: null });
        setForm({ updatedBy: form.updatedBy, notes: "", performance: "" });
        load();
      })
      .catch(function (err) {
        setSubmit({ submitting: false, error: err.message });
      });
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card" onClick={function (e) { e.stopPropagation(); }}>
        <div className="modal-header">
          <h3>{candidateName || candidateId}</h3>
          <button className="modal-close-btn" onClick={onClose} aria-label="Close">×</button>
        </div>

        {data.loading && <p className="muted">Loading…</p>}
        {data.error && <div className="banner banner-error">{data.error}</div>}

        {!data.loading && !data.error && (
          <React.Fragment>
            <div className="stepper">
              {data.stages.map(function (stageName) {
                var entry = data.timeline.filter(function (t) { return t.stage === stageName; })[0];
                var reached = reachedStages.indexOf(stageName) !== -1;
                var isCurrent = stageName === currentStage;
                return (
                  <div className={"stepper-item" + (reached ? " stepper-item-reached" : "") + (isCurrent ? " stepper-item-current" : "")} key={stageName}>
                    <div className="stepper-dot"></div>
                    <div className="stepper-content">
                      <div className="stepper-label">
                        {stageName}
                        {entry && entry.performance && <span className={"badge perf-badge perf-" + entry.performance.toLowerCase()}>{entry.performance}</span>}
                      </div>
                      {entry && (
                        <div className="stepper-meta">
                          {entry.updated_by}{entry.logged_at ? " — " + new Date(entry.logged_at).toLocaleString() : ""}
                          {entry.notes && <div className="stepper-notes">{entry.notes}</div>}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
              {data.stages.length === 0 && <p className="muted">No pipeline stages configured yet.</p>}
            </div>

            {props.editable && blockedReason && (
              <p className="muted stage-advance-blocked">{blockedReason}</p>
            )}

            {props.editable && nextStage && (
              <form className="stage-advance-form" onSubmit={handleAdvance}>
                <h4>Record next stage: {nextStage}</h4>
                <p className="muted">No skipping ahead — this is the only stage that can be recorded next.</p>
                <div className="application-form-grid">
                  <label className="login-field">
                    Updated by
                    <input
                      type="text"
                      value={form.updatedBy}
                      onChange={function (e) { var v = e.target.value; setForm(function (f) { return Object.assign({}, f, { updatedBy: v }); }); }}
                    />
                  </label>
                  <label className="login-field">
                    Performance (optional)
                    <select
                      value={form.performance}
                      onChange={function (e) { var v = e.target.value; setForm(function (f) { return Object.assign({}, f, { performance: v }); }); }}
                    >
                      <option value="">Not rated</option>
                      {PERFORMANCE_RATINGS.map(function (p) { return <option key={p} value={p}>{p}</option>; })}
                    </select>
                  </label>
                </div>
                <label className="login-field">
                  Notes * — how did they do at this stage?
                  <textarea
                    rows={3}
                    required
                    value={form.notes}
                    onChange={function (e) { var v = e.target.value; setForm(function (f) { return Object.assign({}, f, { notes: v }); }); }}
                  />
                </label>
                {submit.error && <div className="banner-error-inline">{submit.error}</div>}
                <button className="btn btn-primary" type="submit" disabled={submit.submitting || !form.updatedBy.trim() || !form.notes.trim()}>
                  {submit.submitting ? "Recording…" : "Record stage"}
                </button>
              </form>
            )}
          </React.Fragment>
        )}
      </div>
    </div>
  );
}
