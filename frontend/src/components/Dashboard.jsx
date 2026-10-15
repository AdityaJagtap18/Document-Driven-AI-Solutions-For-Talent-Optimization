/**
 * Recruiter/Admin Dashboard + HR/Recruiter Reporting Dashboard (combined —
 * see frontend/README.md for why these two backlog items share one view).
 * Candidate list/funnel + cost/token usage, both from the real API shapes:
 * GET /api/candidates and GET /api/observability/summary.
 */
function Dashboard(props) {
  var api = props.api;
  var state = React.useState({ loading: true, error: null, candidates: [], observability: null, filter: "" });
  var data = state[0];
  var setData = state[1];

  React.useEffect(function () {
    var cancelled = false;
    setData(function (d) { return Object.assign({}, d, { loading: true, error: null }); });

    Promise.all([
      api.listCandidates(data.filter || undefined),
      api.observabilitySummary(),
    ])
      .then(function (results) {
        if (cancelled) return;
        setData({ loading: false, error: null, candidates: results[0].candidates, observability: results[1], filter: data.filter });
      })
      .catch(function (err) {
        if (cancelled) return;
        setData(function (d) { return Object.assign({}, d, { loading: false, error: err.message }); });
      });

    return function () { cancelled = true; };
  }, [data.filter]);

  function setFilter(value) {
    setData(function (d) { return Object.assign({}, d, { filter: value }); });
  }

  var tierCounts = { "Recommended for Fast-Track": 0, "To be Considered": 0, "Rejected": 0 };
  data.candidates.forEach(function (c) {
    if (tierCounts[c.ranking_tier] !== undefined) tierCounts[c.ranking_tier] += 1;
  });

  return (
    <div className="view">
      <h2>Recruiter Dashboard</h2>

      {data.error && <div className="banner banner-error">Couldn't load data: {data.error}</div>}

      <div className="stat-row">
        <StatTile label="Fast-Track" value={tierCounts["Recommended for Fast-Track"]} tone="good" />
        <StatTile label="To Be Considered" value={tierCounts["To be Considered"]} tone="neutral" />
        <StatTile label="Rejected" value={tierCounts["Rejected"]} tone="bad" />
      </div>

      {data.observability && (
        <div className="stat-row">
          <StatTile label="LLM Calls" value={data.observability.total_calls} tone="neutral" />
          <StatTile label="Tokens Used" value={data.observability.total_tokens.toLocaleString()} tone="neutral" />
          <StatTile label="Total Cost" value={"$" + data.observability.total_cost_usd.toFixed(4)} tone="neutral" />
        </div>
      )}

      <div className="toolbar">
        <label htmlFor="tier-filter">Filter by tier</label>
        <select id="tier-filter" value={data.filter} onChange={function (e) { setFilter(e.target.value); }}>
          <option value="">All</option>
          <option value="Recommended for Fast-Track">Recommended for Fast-Track</option>
          <option value="To be Considered">To be Considered</option>
          <option value="Rejected">Rejected</option>
        </select>
      </div>

      {data.loading ? (
        <p className="muted">Loading candidates…</p>
      ) : (
        <table className="candidate-table">
          <thead>
            <tr>
              <th>Name</th><th>JD Match</th><th>Type</th><th>Score</th><th>Tier</th>
            </tr>
          </thead>
          <tbody>
            {data.candidates.map(function (c) {
              return (
                <tr key={c.candidate_id}>
                  <td>{c.Name}</td>
                  <td>{c["JD Match"]}</td>
                  <td>{c["Candidate Type"]}</td>
                  <td>{c["Resume Strength Score"]}</td>
                  <td><TierBadge tier={c.ranking_tier} /></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

function StatTile(props) {
  return (
    <div className={"stat-tile stat-tile-" + props.tone}>
      <div className="stat-tile-value">{props.value}</div>
      <div className="stat-tile-label">{props.label}</div>
    </div>
  );
}

function TierBadge(props) {
  var toneClass = props.tier === "Recommended for Fast-Track" ? "badge-good"
    : props.tier === "Rejected" ? "badge-bad" : "badge-neutral";
  return <span className={"badge " + toneClass}>{props.tier}</span>;
}
