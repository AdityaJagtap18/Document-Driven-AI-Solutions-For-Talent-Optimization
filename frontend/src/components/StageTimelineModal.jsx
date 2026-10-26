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
  var permissions = props.permissions || [];
  var canGenerateQuestions = permissions.indexOf("interviews.manage") !== -1;
  var canChat = permissions.indexOf("candidates.read") !== -1;
  var canAssignInterviewer = permissions.indexOf("interviews.assign") !== -1;
  var updatedBy = (props.currentUser && props.currentUser.username) || "";

  var state = React.useState({ loading: true, error: null, timeline: [], stages: [] });
  var data = state[0];
  var setData = state[1];
  var formState = React.useState({ notes: "", performance: "" });
  var form = formState[0];
  var setForm = formState[1];
  var submitState = React.useState({ submitting: false, error: null });
  var submit = submitState[0];
  var setSubmit = submitState[1];

  // Interview Questions (F-06): a candidate_id + jd_id grounded generation
  // action, not a persisted record -- same "run it again to see current
  // output" convention as the RAG Configuration Console's preview and the
  // JD Management page's AI-generation step (neither persists until the
  // user explicitly saves/acts on the result).
  var jdListState = React.useState({ loading: true, error: null, jds: [] });
  var jdList = jdListState[0];
  var setJdList = jdListState[1];
  var selectedJdState = React.useState("");
  var selectedJdId = selectedJdState[0];
  var setSelectedJdId = selectedJdState[1];
  var iqState = React.useState({ status: "idle", error: null, result: null }); // idle | loading | success | error
  var iq = iqState[0];
  var setIq = iqState[1];

  // F-07/F-09 (candidate chatbot, minimal slice): staff-facing Q&A about
  // THIS candidate, grounded in their resume profile -- same "not
  // persisted, ask again to see current output" convention as Interview
  // Questions above. messages is this session's own history only (not
  // saved anywhere), cleared when the modal closes.
  var chatState = React.useState({ messages: [], sending: false, error: null });
  var chat = chatState[0];
  var setChat = chatState[1];
  var chatInputState = React.useState("");
  var chatInput = chatInputState[0];
  var setChatInput = chatInputState[1];

  function handleSendChat(e) {
    e.preventDefault();
    var question = chatInput.trim();
    if (!question || chat.sending) return;
    setChatInput("");
    setChat(function (c) { return { messages: c.messages.concat([{ role: "question", text: question }]), sending: true, error: null }; });
    api.chatCandidateQa(candidateId, question)
      .then(function (res) {
        setChat(function (c) { return { messages: c.messages.concat([{ role: "answer", text: res.answer }]), sending: false, error: null }; });
      })
      .catch(function (err) {
        setChat(function (c) { return { messages: c.messages, sending: false, error: err.message }; });
      });
  }

  React.useEffect(function () {
    if (!canGenerateQuestions) return;
    api.listJds()
      .then(function (res) {
        setJdList({ loading: false, error: null, jds: res.jds || [] });
      })
      .catch(function (err) {
        setJdList({ loading: false, error: err.message, jds: [] });
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canGenerateQuestions]);

  function handleGenerateQuestions() {
    if (!selectedJdId) return;
    setIq({ status: "loading", error: null, result: null });
    api.generateInterviewQuestions(candidateId, selectedJdId)
      .then(function (res) {
        setIq({ status: "success", error: null, result: res });
      })
      .catch(function (err) {
        setIq({ status: "error", error: err.message, result: null });
      });
  }

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

  // Record-level interviewer scoping (auth.py's interviews.assign
  // permission, api_blueprint.py's _interviewer_may_access): only loaded
  // for callers who can actually change it -- a plain interviewer can't
  // see or edit this, they're the thing being scoped, not the scoper.
  var assignState = React.useState({ loading: true, error: null, usernames: [] });
  var assign = assignState[0];
  var setAssign = assignState[1];
  var assignInputState = React.useState("");
  var assignInput = assignInputState[0];
  var setAssignInput = assignInputState[1];
  var assignSubmitState = React.useState({ submitting: false, error: null });
  var assignSubmit = assignSubmitState[0];
  var setAssignSubmit = assignSubmitState[1];

  React.useEffect(function () {
    if (!canAssignInterviewer) return;
    api.getCandidate(candidateId)
      .then(function (candidate) {
        var usernames = candidate.assigned_interviewer_usernames || [];
        setAssign({ loading: false, error: null, usernames: usernames });
        setAssignInput(usernames.join(", "));
      })
      .catch(function (err) {
        setAssign({ loading: false, error: err.message, usernames: [] });
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canAssignInterviewer, candidateId]);

  function handleAssignInterviewer(e) {
    e.preventDefault();
    var usernames = assignInput.split(",").map(function (s) { return s.trim(); }).filter(Boolean);
    setAssignSubmit({ submitting: true, error: null });
    api.assignInterviewer(candidateId, usernames)
      .then(function (res) {
        setAssignSubmit({ submitting: false, error: null });
        setAssign({ loading: false, error: null, usernames: res.assigned_interviewer_usernames });
      })
      .catch(function (err) {
        setAssignSubmit({ submitting: false, error: err.message });
      });
  }

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
    if (!nextStage || !form.notes.trim()) return;
    setSubmit({ submitting: true, error: null });
    api.advanceCandidateStage(candidateId, nextStage, updatedBy, form.notes.trim(), form.performance || undefined)
      .then(function () {
        setSubmit({ submitting: false, error: null });
        setForm({ notes: "", performance: "" });
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
                  <span className="muted">Recording as <strong>{updatedBy}</strong></span>
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
                <button className="btn btn-primary" type="submit" disabled={submit.submitting || !form.notes.trim()}>
                  {submit.submitting ? "Recording…" : "Record stage"}
                </button>
              </form>
            )}

            {canAssignInterviewer && (
              <div className="interview-questions-section">
                <h4>Assigned interviewers</h4>
                <p className="muted">
                  Record-level access control: only these interviewer accounts (plus anyone with a broader role —
                  recruiter, admin, etc.) can view this candidate. A username must already be an active interviewer
                  account.
                </p>
                {assign.loading && <p className="muted">Loading…</p>}
                {assign.error && <div className="banner-error-inline">{assign.error}</div>}
                {!assign.loading && (
                  <form className="application-form-grid" onSubmit={handleAssignInterviewer}>
                    <label className="login-field">
                      Interviewer usernames (comma-separated)
                      <input
                        type="text"
                        placeholder="e.g. persona_interviewer"
                        value={assignInput}
                        onChange={function (e) { setAssignInput(e.target.value); }}
                      />
                    </label>
                    {assignSubmit.error && <div className="banner-error-inline">{assignSubmit.error}</div>}
                    <button className="btn btn-primary" type="submit" disabled={assignSubmit.submitting}>
                      {assignSubmit.submitting ? "Saving…" : "Save assignment"}
                    </button>
                  </form>
                )}
              </div>
            )}

            {canGenerateQuestions && (
              <div className="interview-questions-section">
                <h4>Interview questions</h4>
                <p className="muted">
                  Generate AI-drafted interview questions grounded in this candidate's resume profile and a job
                  description you choose. Not saved automatically — review and use them for this session.
                </p>

                {jdList.loading && <p className="muted">Loading job descriptions…</p>}
                {jdList.error && <div className="banner-error-inline">{jdList.error}</div>}

                {!jdList.loading && !jdList.error && jdList.jds.length === 0 && (
                  <p className="muted">No active job descriptions yet — publish one from Job Descriptions first.</p>
                )}

                {!jdList.loading && !jdList.error && jdList.jds.length > 0 && (
                  <div className="application-form-grid">
                    <label className="login-field">
                      Job description
                      <select
                        value={selectedJdId}
                        onChange={function (e) { setSelectedJdId(e.target.value); setIq({ status: "idle", error: null, result: null }); }}
                      >
                        <option value="">Choose a role…</option>
                        {jdList.jds.map(function (jd) { return <option key={jd.jd_id} value={jd.jd_id}>{jd.title}</option>; })}
                      </select>
                    </label>
                    <button
                      className="btn btn-primary"
                      type="button"
                      disabled={!selectedJdId || iq.status === "loading"}
                      onClick={handleGenerateQuestions}
                    >
                      {iq.status === "loading" ? "Generating…" : "Generate questions"}
                    </button>
                  </div>
                )}

                {iq.status === "error" && <div className="banner-error-inline">{iq.error}</div>}

                {iq.status === "success" && iq.result && iq.result.questions.length === 0 && (
                  <p className="muted">No usable questions were generated — try again or choose a different role.</p>
                )}

                {iq.status === "success" && iq.result && iq.result.questions.length > 0 && (
                  <ol className="interview-questions-list">
                    {iq.result.questions.map(function (q, idx) { return <li key={idx}>{q}</li>; })}
                  </ol>
                )}
              </div>
            )}

            {canChat && (
              <div className="candidate-chat-section">
                <h4>Ask about this candidate</h4>
                <p className="muted">
                  Grounded in this candidate's resume profile and hiring record. Staff-facing only -- this app has no
                  candidate login, so this is a tool for recruiters/interviewers, not a candidate-facing chatbot.
                  Not saved; this conversation clears when you close this window.
                </p>
                {chat.messages.length > 0 && (
                  <div className="candidate-chat-history">
                    {chat.messages.map(function (m, idx) {
                      return <div key={idx} className={"candidate-chat-message candidate-chat-" + m.role}>{m.text}</div>;
                    })}
                  </div>
                )}
                {chat.error && <div className="banner-error-inline">{chat.error}</div>}
                <form className="candidate-chat-form" onSubmit={handleSendChat}>
                  <input
                    type="text" placeholder="e.g. What's their strongest skill for this role?"
                    value={chatInput}
                    onChange={function (e) { setChatInput(e.target.value); }}
                  />
                  <button className="btn btn-primary" type="submit" disabled={chat.sending || !chatInput.trim()}>
                    {chat.sending ? "Asking…" : "Ask"}
                  </button>
                </form>
              </div>
            )}
          </React.Fragment>
        )}
      </div>
    </div>
  );
}
