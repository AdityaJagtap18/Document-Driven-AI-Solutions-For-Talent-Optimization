/**
 * Recruiter/Admin Dashboard + HR/Recruiter Reporting Dashboard (combined —
 * see frontend/README.md for why these two backlog items share one view).
 * Candidate list/funnel + cost/token usage, both from the real API shapes:
 * GET /api/candidates and GET /api/observability/summary.
 *
 * Rows expand in place to a full recruiter-facing profile (contact info,
 * skills, projects, certifications, culture fit, red flags, HR notes,
 * etc.) — whatever fields the candidate record actually has. Real
 * candidates (src/data/candidates.json, see export_real_candidates.py)
 * carry the full set; the small FALLBACK_CANDIDATES set in
 * mockApiClient.js only has the table-summary fields, so the detail panel
 * only renders a field when it's actually present rather than showing
 * empty sections for every mock candidate.
 */
function Dashboard(props) {
  var api = props.api;
  var state = React.useState({ loading: true, error: null, candidates: [], observability: null, filter: "" });
  var data = state[0];
  var setData = state[1];
  var expandedState = React.useState(null);
  var expandedId = expandedState[0];
  var setExpandedId = expandedState[1];

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
              var isExpanded = expandedId === c.candidate_id;
              return (
                <React.Fragment key={c.candidate_id}>
                  <tr
                    className="candidate-row-clickable"
                    onClick={function () { setExpandedId(isExpanded ? null : c.candidate_id); }}
                  >
                    <td>{c.Name}</td>
                    <td>{c["JD Match"]}</td>
                    <td>{c["Candidate Type"]}</td>
                    <td>{c["Resume Strength Score"]}</td>
                    <td><TierBadge tier={c.ranking_tier} /></td>
                  </tr>
                  {isExpanded && (
                    <tr>
                      <td colSpan={5}><CandidateDetail candidate={c} /></td>
                    </tr>
                  )}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

/** Full recruiter-facing profile for one candidate — only renders a field
 * when it's actually present on the record (see Dashboard doc comment). */
function CandidateDetail(props) {
  var c = props.candidate;

  function hasContent(value) {
    if (value === null || value === undefined || value === "") return false;
    if (Array.isArray(value)) return value.length > 0;
    return true;
  }

  function Field(fieldProps) {
    if (!hasContent(fieldProps.value)) return null;
    return (
      <div className="detail-row">
        <div className="detail-label">{fieldProps.label}</div>
        <div className="detail-value">{fieldProps.children || fieldProps.value}</div>
      </div>
    );
  }

  function ChipList(listProps) {
    if (!hasContent(listProps.items)) return null;
    return (
      <div className="chip-list">
        {listProps.items.map(function (item, i) {
          return <span className="chip" key={i}>{item}</span>;
        })}
      </div>
    );
  }

  // Field (above) only has a `value` prop to check visibility against, so
  // it can't tell whether a <ChipList> passed as children is actually
  // empty — it would render the label with nothing next to it. This
  // variant checks the underlying items array itself and hides the whole
  // row (label included) when there's nothing to show.
  function ChipField(fieldProps) {
    if (!hasContent(fieldProps.items)) return null;
    return (
      <div className="detail-row">
        <div className="detail-label">{fieldProps.label}</div>
        <div className="detail-value"><ChipList items={fieldProps.items} /></div>
      </div>
    );
  }

  var missing = c["Missing Keywords"];
  var missingFlat = missing && typeof missing === "object" && !Array.isArray(missing)
    ? Object.keys(missing).reduce(function (acc, k) { return acc.concat(missing[k] || []); }, [])
    : missing;

  return (
    <div className="detail-panel">
      <div className="detail-section">
        <h4>Contact</h4>
        <Field label="Email" value={c.Email} />
        <Field label="Phone" value={c.Phone} />
        <Field label="LinkedIn" value={c.LinkedIn} />
      </div>

      {hasContent(c["Profile Summary"]) && (
        <div className="detail-section">
          <h4>Profile Summary</h4>
          <p>{c["Profile Summary"]}</p>
        </div>
      )}

      {(hasContent(c["Top Matching Keywords"]) || hasContent(missingFlat)) && (
        <div className="detail-section">
          <h4>Skills</h4>
          <ChipField label="Matching" items={c["Top Matching Keywords"]} />
          <ChipField label="Missing" items={missingFlat} />
        </div>
      )}

      {hasContent(c["Projects"]) && (
        <div className="detail-section">
          <h4>Projects</h4>
          {c["Projects"].map(function (p, i) {
            return (
              <div className="detail-project" key={i}>
                <strong>{p["Project Name"]}</strong>
                {p["Relevance to JD"] && <span className="muted"> — {p["Relevance to JD"]} relevance</span>}
                {p["Impact"] && <p className="muted">{p["Impact"]}</p>}
                <ChipList items={p["Technologies Used"]} />
              </div>
            );
          })}
        </div>
      )}

      {(hasContent(c["Certifications & Courses"]) || hasContent(c["Skills That Will Contribute to the Company"]) || hasContent(c["Soft Skills & Leadership Qualities"])) && (
        <div className="detail-section">
          <h4>Certifications & Soft Skills</h4>
          <ChipField label="Certifications" items={c["Certifications & Courses"]} />
          <ChipField label="Will contribute" items={c["Skills That Will Contribute to the Company"]} />
          <ChipField label="Soft skills" items={c["Soft Skills & Leadership Qualities"]} />
        </div>
      )}

      <div className="detail-section">
        <h4>Fit & Experience</h4>
        <Field label="Industry experience" value={c["Industry Experience"]} />
        <Field label="Culture fit" value={c["Culture Fit Assessment"]} />
        <Field label="Growth potential" value={c["Candidate's Growth Potential"]} />
        <Field label="Effort needed" value={c["Effort Needed by the Company"]} />
        <Field label="Relevant experience (yrs)" value={c["Relevant Experience (yrs)"]} />
        <Field label="Resume format quality" value={c["Resume Format Quality"]} />
        <Field label="Employment gaps" value={c["Employment Gaps Detected"] ? "Yes" : null} />
      </div>

      {(hasContent(c["Potential Concerns"]) || hasContent(c["Red Flags & Risk Analysis"])) && (
        <div className="detail-section detail-section-risk">
          <h4>Risk Signals</h4>
          <ChipField label="Potential concerns" items={c["Potential Concerns"]} />
          <ChipField label="Red flags" items={c["Red Flags & Risk Analysis"]} />
        </div>
      )}

      {hasContent(c["HR Notes"]) && (
        <div className="detail-section">
          <h4>HR Notes</h4>
          <p>{c["HR Notes"]}</p>
        </div>
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
