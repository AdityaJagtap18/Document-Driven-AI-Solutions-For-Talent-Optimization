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
 */
function StageTimelineModal(props) {
  var api = props.api;
  var candidateId = props.candidateId;
  var candidateName = props.candidateName;
  var onClose = props.onClose;

  var state = React.useState({ loading: true, error: null, timeline: [], stages: [] });
  var data = state[0];
  var setData = state[1];
  var formState = React.useState({ stage: "", updatedBy: "", notes: "" });
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

  function handleAdvance(e) {
    e.preventDefault();
    if (!form.stage || !form.updatedBy.trim()) return;
    setSubmit({ submitting: true, error: null });
    api.advanceCandidateStage(candidateId, form.stage, form.updatedBy.trim(), form.notes.trim())
      .then(function () {
        setSubmit({ submitting: false, error: null });
        setForm({ stage: "", updatedBy: form.updatedBy, notes: "" });
        load();
      })
      .catch(function (err) {
        setSubmit({ submitting: false, error: err.message });
      });
  }

  var reachedStages = data.timeline.map(function (t) { return t.stage; });
  var currentStage = data.timeline.length ? data.timeline[data.timeline.length - 1].stage : null;

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
                      <div className="stepper-label">{stageName}</div>
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

            {props.editable && data.stages.length > 0 && (
              <form className="stage-advance-form" onSubmit={handleAdvance}>
                <h4>Record a stage</h4>
                <div className="application-form-grid">
                  <label className="login-field">
                    Stage
                    <select
                      value={form.stage}
                      onChange={function (e) { var v = e.target.value; setForm(function (f) { return Object.assign({}, f, { stage: v }); }); }}
                    >
                      <option value="">Select a stage…</option>
                      {data.stages.map(function (s) { return <option key={s} value={s}>{s}</option>; })}
                    </select>
                  </label>
                  <label className="login-field">
                    Updated by
                    <input
                      type="text"
                      value={form.updatedBy}
                      onChange={function (e) { var v = e.target.value; setForm(function (f) { return Object.assign({}, f, { updatedBy: v }); }); }}
                    />
                  </label>
                </div>
                <label className="login-field">
                  Notes (optional)
                  <input
                    type="text"
                    value={form.notes}
                    onChange={function (e) { var v = e.target.value; setForm(function (f) { return Object.assign({}, f, { notes: v }); }); }}
                  />
                </label>
                {submit.error && <div className="banner-error-inline">{submit.error}</div>}
                <button className="btn btn-primary" type="submit" disabled={submit.submitting || !form.stage || !form.updatedBy.trim()}>
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
