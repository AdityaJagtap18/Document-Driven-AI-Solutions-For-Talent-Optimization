/**
 * Human Review Queue — formalizes FDE Stage 23 (HITL). Shows the "To be
 * Considered" tier (the one that previously only got an emailed CSV, per
 * the brownfield audit) and lets a recruiter approve/reject each one,
 * which calls POST /api/candidates/{id}/review and logs to audit_log.
 */
function ReviewQueue(props) {
  var api = props.api;
  var state = React.useState({ loading: true, error: null, candidates: [], reviewerName: "" });
  var data = state[0];
  var setData = state[1];
  var actionState = React.useState({}); // candidate_id -> "pending" | "done" | error message
  var actions = actionState[0];
  var setActions = actionState[1];

  function load() {
    setData(function (d) { return Object.assign({}, d, { loading: true, error: null }); });
    api.listCandidates("To be Considered")
      .then(function (result) {
        setData(function (d) { return Object.assign({}, d, { loading: false, candidates: result.candidates }); });
      })
      .catch(function (err) {
        setData(function (d) { return Object.assign({}, d, { loading: false, error: err.message }); });
      });
  }

  React.useEffect(load, []);

  function handleDecision(candidateId, decision) {
    if (!data.reviewerName.trim()) {
      setActions(function (a) {
        var next = Object.assign({}, a);
        next[candidateId] = "Enter your name before reviewing.";
        return next;
      });
      return;
    }
    setActions(function (a) {
      var next = Object.assign({}, a);
      next[candidateId] = "pending";
      return next;
    });
    api.reviewCandidate(candidateId, decision, data.reviewerName.trim(), "")
      .then(function () {
        setActions(function (a) {
          var next = Object.assign({}, a);
          next[candidateId] = "done";
          return next;
        });
        setData(function (d) {
          return Object.assign({}, d, { candidates: d.candidates.filter(function (c) { return c.candidate_id !== candidateId; }) });
        });
      })
      .catch(function (err) {
        setActions(function (a) {
          var next = Object.assign({}, a);
          next[candidateId] = "Error: " + err.message;
          return next;
        });
      });
  }

  return (
    <div className="view">
      <h2>Human Review Queue</h2>
      <p className="muted">Candidates in the "To be Considered" tier — approve or reject each, with a reason trail in the audit log.</p>

      <div className="toolbar">
        <label htmlFor="reviewer-name">Reviewing as</label>
        <input
          id="reviewer-name"
          type="text"
          placeholder="your name or email"
          value={data.reviewerName}
          onChange={function (e) {
            var value = e.target.value;
            setData(function (d) { return Object.assign({}, d, { reviewerName: value }); });
          }}
        />
      </div>

      {data.error && <div className="banner banner-error">Couldn't load queue: {data.error}</div>}
      {data.loading && <p className="muted">Loading…</p>}
      {!data.loading && data.candidates.length === 0 && !data.error && (
        <p className="muted">Nothing waiting for review.</p>
      )}

      <div className="review-list">
        {data.candidates.map(function (c) {
          var action = actions[c.candidate_id];
          return (
            <div className="review-card" key={c.candidate_id}>
              <div className="review-card-main">
                <strong>{c.Name}</strong>
                <span className="muted"> — {c["JD Match"]} match, {c["Candidate Type"]}</span>
                <p className="review-summary">{c["Profile Summary"]}</p>
              </div>
              <div className="review-card-actions">
                {action === "pending" && <span className="muted">Saving…</span>}
                {action === "done" && <span className="badge badge-good">Recorded</span>}
                {action && action !== "pending" && action !== "done" && <span className="banner-error-inline">{action}</span>}
                {(!action || (action !== "pending" && action !== "done")) && (
                  <React.Fragment>
                    <button className="btn btn-approve" onClick={function () { handleDecision(c.candidate_id, "approve"); }}>
                      Approve
                    </button>
                    <button className="btn btn-reject" onClick={function () { handleDecision(c.candidate_id, "reject"); }}>
                      Reject
                    </button>
                  </React.Fragment>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
