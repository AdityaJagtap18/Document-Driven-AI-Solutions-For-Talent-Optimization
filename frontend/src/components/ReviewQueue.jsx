/**
 * Human Review Queue — formalizes FDE Stage 23 (HITL). Two distinct
 * actions here:
 *
 * 1. Individual approve/reject (unchanged) — a recruiter's own real
 *    decision on one candidate, calls POST /api/candidates/{id}/review
 *    immediately. This already was human-in-the-loop, so it doesn't need
 *    any extra gate.
 *
 * 2. Bulk propose (new) — select several candidates (any tier, via the
 *    filter below, not just "To be Considered") and propose accepting or
 *    rejecting all of them at once. This does NOT change any candidate —
 *    it only records a pending proposal (POST /api/bulk-decisions) that
 *    an admin must separately approve before anything actually happens.
 *    The point: the AI/scoring pipeline's ranking_tier is only ever a
 *    RECOMMENDATION, and a recruiter acting on a batch of them in bulk
 *    doesn't finalize anything either — only an explicit admin approval
 *    does (see Pipeline.jsx's "Pending Bulk Approvals" panel).
 */
var TIER_OPTIONS = ["To be Considered", "Recommended for Fast-Track", "Rejected"];

function ReviewQueue(props) {
  var api = props.api;
  var permissions = props.permissions || [];
  var reviewerName = (props.currentUser && props.currentUser.username) || "";
  var state = React.useState({ loading: true, error: null, candidates: [], tier: "To be Considered" });
  var data = state[0];
  var setData = state[1];
  var actionState = React.useState({}); // candidate_id -> "pending" | "done" | error message
  var actions = actionState[0];
  var setActions = actionState[1];
  var modalState = React.useState(null); // { candidateId, candidateName } | null
  var modalCandidate = modalState[0];
  var setModalCandidate = modalState[1];
  var configOpenState = React.useState(false);
  var configOpen = configOpenState[0];
  var setConfigOpen = configOpenState[1];
  var selectedState = React.useState({}); // candidate_id -> true
  var selected = selectedState[0];
  var setSelected = selectedState[1];
  var bulkState = React.useState({ submitting: false, error: null, result: null });
  var bulk = bulkState[0];
  var setBulk = bulkState[1];

  function load() {
    setData(function (d) { return Object.assign({}, d, { loading: true, error: null }); });
    api.listCandidates(data.tier)
      .then(function (result) {
        setData(function (d) { return Object.assign({}, d, { loading: false, candidates: result.candidates }); });
        setSelected({});
      })
      .catch(function (err) {
        setData(function (d) { return Object.assign({}, d, { loading: false, error: err.message }); });
      });
  }

  React.useEffect(load, [data.tier]);

  function setTier(tier) {
    setData(function (d) { return Object.assign({}, d, { tier: tier }); });
  }

  function handleDecision(candidateId, decision) {
    setActions(function (a) {
      var next = Object.assign({}, a);
      next[candidateId] = "pending";
      return next;
    });
    api.reviewCandidate(candidateId, decision, reviewerName, "")
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

  function toggleSelected(candidateId) {
    setSelected(function (s) {
      var next = Object.assign({}, s);
      if (next[candidateId]) delete next[candidateId]; else next[candidateId] = true;
      return next;
    });
  }

  var selectedIds = Object.keys(selected);

  function handleBulkPropose(decision) {
    if (selectedIds.length === 0) return;
    setBulk({ submitting: true, error: null, result: null });
    api.proposeBulkDecision(decision, selectedIds, reviewerName, "")
      .then(function (result) {
        setBulk({ submitting: false, error: null, result: result });
        setSelected({});
      })
      .catch(function (err) {
        setBulk({ submitting: false, error: err.message, result: null });
      });
  }

  return (
    <div className="view">
      <h2>Human Review Queue</h2>
      <p className="muted">
        The AI only recommends a tier — approve/reject here (individually or, via the checkboxes, as a
        batch proposal) is what actually decides. Bulk proposals need separate admin approval before
        anything changes; individual approve/reject below takes effect immediately.
      </p>

      <div className="toolbar">
        <span id="reviewer-name" className="muted">Reviewing as <strong>{reviewerName}</strong></span>
        <label htmlFor="tier-filter">Tier</label>
        <select id="tier-filter" value={data.tier} onChange={function (e) { setTier(e.target.value); }}>
          {TIER_OPTIONS.map(function (t) { return <option key={t} value={t}>{tierDisplayLabel(t)}</option>; })}
        </select>
        <button className="btn" onClick={function () { setConfigOpen(true); }}>Configure Stages</button>
      </div>

      {selectedIds.length > 0 && (
        <div className="bulk-toolbar">
          <span>{selectedIds.length} selected</span>
          <button
            className="btn btn-approve"
            disabled={bulk.submitting}
            onClick={function () { handleBulkPropose("approve"); }}
          >
            Propose Bulk Accept
          </button>
          <button
            className="btn btn-reject"
            disabled={bulk.submitting}
            onClick={function () { handleBulkPropose("reject"); }}
          >
            Propose Bulk Reject
          </button>
        </div>
      )}
      {bulk.result && (
        <div className="banner banner-success">
          {bulk.result.message} ({bulk.result.candidate_count} candidate(s))
        </div>
      )}
      {bulk.error && <div className="banner banner-error">{bulk.error}</div>}

      {data.error && <div className="banner banner-error">Couldn't load queue: {data.error}</div>}
      {data.loading && <p className="muted">Loading…</p>}
      {!data.loading && data.candidates.length === 0 && !data.error && (
        <p className="muted">No candidates in this tier.</p>
      )}

      <div className="review-list">
        {data.candidates.map(function (c) {
          var action = actions[c.candidate_id];
          return (
            <div className="review-card" key={c.candidate_id}>
              <input
                type="checkbox"
                className="review-card-checkbox"
                checked={!!selected[c.candidate_id]}
                onChange={function () { toggleSelected(c.candidate_id); }}
              />
              <div className="review-card-main">
                <strong>{c.Name}</strong>
                <span className="muted"> — {c["JD Match"]} match, {c["Candidate Type"]}</span>
                <p className="review-summary">{c["Profile Summary"]}</p>
              </div>
              <div className="review-card-actions">
                <button
                  className="btn"
                  onClick={function () { setModalCandidate({ candidateId: c.candidate_id, candidateName: c.Name }); }}
                >
                  View Timeline
                </button>
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

      {modalCandidate && (
        <StageTimelineModal
          api={api}
          permissions={permissions}
          currentUser={props.currentUser}
          candidateId={modalCandidate.candidateId}
          candidateName={modalCandidate.candidateName}
          editable={true}
          onClose={function () { setModalCandidate(null); }}
        />
      )}

      {configOpen && (
        <PipelineConfigModal api={api} onClose={function () { setConfigOpen(false); }} />
      )}
    </div>
  );
}
