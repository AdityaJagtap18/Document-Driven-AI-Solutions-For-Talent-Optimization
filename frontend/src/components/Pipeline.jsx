/**
 * Applicant Pipeline: which hiring stage each applicant is actually at,
 * one Kanban-style column per stage. Built from two real sources —
 * api.listCandidates() (whoever's resume has been parsed/scored) and
 * api.listAuditLog() (every application_submitted/document_verification/
 * human_review_* event, GET /audit-log, azure-functions/api_blueprint.py)
 * — not a separate hand-maintained status field, so it can't drift from
 * what actually happened.
 *
 * "Real time": this is periodic polling (every 20s, see POLL_INTERVAL_MS
 * below) plus a manual refresh button, NOT a push-based live feed --
 * there's no WebSocket/SignalR layer in this stack. Labeled as
 * "auto-refreshing" in the UI rather than oversold as instant.
 *
 * Stage order (furthest-evidence-wins, computed per candidate from the
 * audit trail, not a field anyone sets directly):
 *   Applied -> Parsed & Scored -> Document Verified -> Reviewed
 *
 * A real gap this surfaces rather than hides: a candidate who just
 * submitted via the Candidate Portal (Applied) but whose resume the
 * blob-triggered function (function_app.py) hasn't processed yet has NO
 * candidate record at all -- they only exist as an application_submitted
 * audit entry. This view is the one place that "in flight, not yet
 * parsed" state is visible; everywhere else in the app only shows
 * candidates who've already been scored.
 */
var POLL_INTERVAL_MS = 20000;

var STAGES = [
  { key: "applied", label: "Applied" },
  { key: "scored", label: "Parsed & Scored" },
  { key: "doc_verified", label: "Document Verified" },
  { key: "reviewed", label: "Reviewed" },
];

function initialsForPipeline(name) {
  var parts = (name || "").trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  return (parts[0][0] + (parts[1] ? parts[1][0] : "")).toUpperCase();
}

function computeStage(candidateId, candidatesById, auditByCandidate) {
  var candidate = candidatesById[candidateId];
  var entries = auditByCandidate[candidateId] || [];
  var actions = entries.map(function (e) { return e.action; });

  if (actions.indexOf("human_review_approve") !== -1) return { key: "reviewed", detail: "Approved" };
  if (actions.indexOf("human_review_reject") !== -1) return { key: "reviewed", detail: "Rejected" };
  if (actions.indexOf("document_verification") !== -1) return { key: "doc_verified", detail: null };
  // Still just the AI's own suggestion at this stage (no human decision
  // recorded yet) -- shown with the softer framing; contrast with the
  // "Approved"/"Rejected" cases above, which describe a REAL human
  // decision already made and correctly stay as plain past-tense text.
  if (candidate) return { key: "scored", detail: tierDisplayLabel(candidate.ranking_tier) };
  if (actions.indexOf("application_submitted") !== -1) return { key: "applied", detail: "Awaiting processing" };
  return { key: "scored", detail: null };
}

function Pipeline(props) {
  var api = props.api;
  var permissions = props.permissions || [];
  var currentUser = props.currentUser;
  var state = React.useState({ loading: true, error: null, candidates: [], auditEntries: [], lastUpdated: null });
  var data = state[0];
  var setData = state[1];
  var searchState = React.useState("");
  var search = searchState[0];
  var setSearch = searchState[1];
  var modalState = React.useState(null); // { candidateId, candidateName } | null
  var modalCandidate = modalState[0];
  var setModalCandidate = modalState[1];
  var configOpenState = React.useState(false);
  var configOpen = configOpenState[0];
  var setConfigOpen = configOpenState[1];
  var pendingState = React.useState({ loading: true, error: null, decisions: [] });
  var pending = pendingState[0];
  var setPending = pendingState[1];

  function loadPending() {
    return api.listBulkDecisions("pending")
      .then(function (result) {
        setPending({ loading: false, error: null, decisions: result.decisions });
      })
      .catch(function (err) {
        setPending({ loading: false, error: err.message, decisions: [] });
      });
  }

  function load() {
    return Promise.all([api.listCandidates(), api.listAuditLog()])
      .then(function (results) {
        setData({
          loading: false, error: null,
          candidates: results[0].candidates, auditEntries: results[1].entries,
          lastUpdated: new Date(),
        });
      })
      .catch(function (err) {
        setData(function (d) { return Object.assign({}, d, { loading: false, error: err.message }); });
      });
  }

  React.useEffect(function () {
    load();
    loadPending();
    var interval = setInterval(function () { load(); loadPending(); }, POLL_INTERVAL_MS);
    return function () { clearInterval(interval); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (data.loading) return <div className="view"><h2>Applicant Pipeline</h2><p className="muted">Loading…</p></div>;
  if (data.error) return <div className="view"><h2>Applicant Pipeline</h2><div className="banner banner-error">{data.error}</div></div>;

  var candidatesById = {};
  data.candidates.forEach(function (c) { candidatesById[c.candidate_id] = c; });

  var auditByCandidate = {};
  data.auditEntries.forEach(function (e) {
    (auditByCandidate[e.candidate_id] = auditByCandidate[e.candidate_id] || []).push(e);
  });

  var allIds = Object.keys(candidatesById);
  Object.keys(auditByCandidate).forEach(function (id) {
    if (allIds.indexOf(id) === -1) allIds.push(id);
  });

  function displayName(id) {
    if (candidatesById[id]) return candidatesById[id].Name || id;
    var entries = auditByCandidate[id] || [];
    var withName = entries.filter(function (e) { return e.full_name; })[0];
    return (withName && withName.full_name) || id;
  }

  var searchLower = search.trim().toLowerCase();
  var columns = STAGES.map(function (stage) {
    var items = allIds
      .filter(function (id) { return computeStage(id, candidatesById, auditByCandidate).key === stage.key; })
      .filter(function (id) { return !searchLower || displayName(id).toLowerCase().indexOf(searchLower) !== -1; })
      .map(function (id) {
        return { id: id, name: displayName(id), detail: computeStage(id, candidatesById, auditByCandidate).detail };
      });
    return { stage: stage, items: items };
  });

  return (
    <div className="view">
      <h2>Applicant Pipeline</h2>
      <p className="muted">
        Auto-refreshing every {POLL_INTERVAL_MS / 1000}s (polling, not a push-based live feed — see
        Pipeline.jsx's doc comment). Last updated {data.lastUpdated ? data.lastUpdated.toLocaleTimeString() : "—"}.
      </p>

      <div className="toolbar">
        <input
          type="search"
          placeholder="Search by name…"
          value={search}
          onChange={function (e) { setSearch(e.target.value); }}
        />
        <button className="btn" onClick={load}>Refresh now</button>
        <button className="btn" onClick={function () { setConfigOpen(true); }}>Configure Stages</button>
      </div>

      <BulkApprovalsPanel api={api} pending={pending} currentUser={currentUser} onReviewed={function () { loadPending(); load(); }} />

      <div className="pipeline-board">
        {columns.map(function (col) {
          return (
            <div className="pipeline-column" key={col.stage.key}>
              <div className="pipeline-column-header">
                <span>{col.stage.label}</span>
                <span className="pipeline-column-count">{col.items.length}</span>
              </div>
              <div className="pipeline-column-body">
                {col.items.length === 0 && <p className="muted pipeline-empty">None</p>}
                {col.items.map(function (item) {
                  return (
                    <div
                      className="pipeline-card pipeline-card-clickable"
                      key={item.id}
                      onClick={function () { setModalCandidate({ candidateId: item.id, candidateName: item.name }); }}
                    >
                      <div className="avatar-circle">{initialsForPipeline(item.name)}</div>
                      <div>
                        <div className="pipeline-card-name">{item.name}</div>
                        {item.detail && <div className="pipeline-card-detail">{item.detail}</div>}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>

      {modalCandidate && (
        <StageTimelineModal
          api={api}
          permissions={permissions}
          currentUser={currentUser}
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

/** Admin's approval queue for recruiter-proposed bulk accept/reject
 * batches (see ReviewQueue.jsx's doc comment). Nothing a recruiter
 * proposes in bulk takes effect until it's acted on here -- that's the
 * entire point of the gate. */
function BulkApprovalsPanel(props) {
  var api = props.api;
  var pending = props.pending;
  var onReviewed = props.onReviewed;
  var reviewerName = (props.currentUser && props.currentUser.username) || "";
  var actionState = React.useState({}); // decision id -> "pending" | error message
  var actions = actionState[0];
  var setActions = actionState[1];

  function setAction(id, value) {
    setActions(function (a) {
      var next = Object.assign({}, a);
      if (value === null) delete next[id]; else next[id] = value;
      return next;
    });
  }

  function handle(decisionId, type) {
    setAction(decisionId, "pending");
    var call = type === "approve" ? api.approveBulkDecision(decisionId, reviewerName) : api.rejectBulkDecision(decisionId, reviewerName, "");
    call
      .then(function () {
        setAction(decisionId, null);
        onReviewed();
      })
      .catch(function (err) {
        setAction(decisionId, "Error: " + err.message);
      });
  }

  if (pending.loading) return null;
  if (pending.error) return <div className="banner banner-error">Couldn't load pending approvals: {pending.error}</div>;
  if (pending.decisions.length === 0) return null;

  return (
    <div className="bulk-approvals-panel">
      <div className="bulk-approvals-header">
        <h4>Pending Bulk Approvals ({pending.decisions.length})</h4>
        <span className="muted">Reviewing as <strong>{reviewerName}</strong></span>
      </div>
      {pending.decisions.map(function (d) {
        var action = actions[d.id];
        return (
          <div className="bulk-approval-card" key={d.id}>
            <div>
              <strong>{d.decision === "approve" ? "Bulk Accept" : "Bulk Reject"}</strong>
              <span className="muted"> — {d.candidate_ids.length} candidate(s), proposed by {d.proposed_by}</span>
              {d.notes && <p className="review-summary">{d.notes}</p>}
            </div>
            <div className="review-card-actions">
              {action === "pending" && <span className="muted">Saving…</span>}
              {action && action !== "pending" && <span className="banner-error-inline">{action}</span>}
              {action !== "pending" && (
                <React.Fragment>
                  <button className="btn btn-approve" onClick={function () { handle(d.id, "approve"); }}>Approve</button>
                  <button className="btn btn-reject" onClick={function () { handle(d.id, "reject"); }}>Reject</button>
                </React.Fragment>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
