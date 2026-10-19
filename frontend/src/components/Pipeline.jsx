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
  if (candidate) return { key: "scored", detail: candidate.ranking_tier };
  if (actions.indexOf("application_submitted") !== -1) return { key: "applied", detail: "Awaiting processing" };
  return { key: "scored", detail: null };
}

function Pipeline(props) {
  var api = props.api;
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
    var interval = setInterval(load, POLL_INTERVAL_MS);
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
