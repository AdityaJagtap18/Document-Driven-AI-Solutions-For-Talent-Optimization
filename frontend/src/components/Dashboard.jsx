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
function initialsFor(name) {
  var parts = (name || "").trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  return (parts[0][0] + (parts[1] ? parts[1][0] : "")).toUpperCase();
}

var SORTABLE_COLUMNS = [
  { key: "Name", label: "Name" },
  { key: "JD Match", label: "JD Match" },
  { key: "Candidate Type", label: "Type" },
  { key: "Resume Strength Score", label: "Score" },
  { key: "ranking_tier", label: "Tier" },
];

function sortValue(candidate, key) {
  var v = candidate[key];
  if (key === "JD Match" && typeof v === "string") return parseFloat(v) || 0;
  return v;
}

function greetingForHour(hour) {
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  return "Good evening";
}

/** Last `days` calendar days as YYYY-MM-DD keys, oldest first — the
 * x-axis for the Hiring Trend chart below, independent of whether any
 * audit events actually land on a given day (a day with none is a real
 * zero, not a gap). */
function recentDayKeys(days) {
  var out = [];
  var now = new Date();
  for (var i = days - 1; i >= 0; i--) {
    var d = new Date(now.getFullYear(), now.getMonth(), now.getDate() - i);
    out.push(d.toISOString().slice(0, 10));
  }
  return out;
}

function Dashboard(props) {
  var api = props.api;
  var currentUser = props.currentUser;
  var firstName = ((currentUser && (currentUser.display_name || currentUser.username)) || "").trim().split(/\s+/)[0];
  var filterState = React.useState("");
  var filter = filterState[0];
  var setFilter = filterState[1];
  var expandedState = React.useState(null);
  var expandedId = expandedState[0];
  var setExpandedId = expandedState[1];
  var searchState = React.useState("");
  var search = searchState[0];
  var setSearch = searchState[1];
  var sortState = React.useState({ key: null, dir: 1 });
  var sort = sortState[0];
  var setSort = sortState[1];

  // Hiring Pipeline funnel + Hiring Trend need the same audit trail
  // Pipeline.jsx computes candidate stages from (see its computeStage/
  // STAGES, reused here as-is rather than re-deriving the same logic) --
  // and Open Positions needs the JD records, not the plain jd_folder
  // listing. Plain state, not report-snapshot-cached: this is the same
  // "always current, not yesterday's cache" operational page as the rest
  // of the Dashboard (see reportSnapshot.js's doc comment).
  var extrasState = React.useState({ loading: true, error: null, auditEntries: [], jds: [], jdsError: null });
  var extras = extrasState[0];
  var setExtras = extrasState[1];

  function loadExtras() {
    setExtras(function (e) { return Object.assign({}, e, { loading: true, error: null }); });
    return api.listAuditLog()
      .then(function (auditResult) {
        return api.listJdRecords("published")
          .then(function (jdResult) {
            setExtras({ loading: false, error: null, auditEntries: auditResult.entries, jds: jdResult.jds, jdsError: null });
          })
          .catch(function (jdErr) {
            // Role may not have jds.read, or /api/manage/jds may not be
            // deployed yet -- Open Positions just hides itself below
            // rather than failing the whole dashboard.
            setExtras({ loading: false, error: null, auditEntries: auditResult.entries, jds: [], jdsError: jdErr.message });
          });
      })
      .catch(function (err) {
        setExtras(function (e) { return Object.assign({}, e, { loading: false, error: err.message }); });
      });
  }

  React.useEffect(function () {
    loadExtras();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Snapshot-cached, per the "reports show their last snapshot, never
  // reload on revisit" requirement -- keyed by filter, so switching the
  // tier filter shows that filter's own cached snapshot instantly once
  // it's been loaded once, rather than always re-fetching.
  var snapshot = CDPI_REPORT_SNAPSHOT.useReportSnapshot("dashboard_candidates:" + (filter || "all"), function () {
    return api.listCandidates(filter || undefined);
  });
  var observabilitySnapshot = CDPI_REPORT_SNAPSHOT.useReportSnapshot("dashboard_observability", function () {
    return api.observabilitySummary();
  });
  var meta = snapshot.state;
  var data = {
    loading: meta.loading && !meta.data, error: meta.error,
    candidates: (meta.data && meta.data.candidates) || [],
    observability: observabilitySnapshot.state.data,
  };

  function refreshAll() {
    snapshot.refresh();
    observabilitySnapshot.refresh();
    loadExtras();
  }

  var tierCounts = { "Recommended for Fast-Track": 0, "To be Considered": 0, "Rejected": 0 };
  data.candidates.forEach(function (c) {
    if (tierCounts[c.ranking_tier] !== undefined) tierCounts[c.ranking_tier] += 1;
  });
  var totalCandidates = data.candidates.length;

  // Hiring Pipeline funnel -- same per-candidate stage derivation as
  // Pipeline.jsx (STAGES/computeStage, global functions defined there),
  // reused rather than duplicated so the two views can't drift.
  var candidatesById = {};
  data.candidates.forEach(function (c) { candidatesById[c.candidate_id] = c; });
  var auditByCandidate = {};
  extras.auditEntries.forEach(function (e) {
    (auditByCandidate[e.candidate_id] = auditByCandidate[e.candidate_id] || []).push(e);
  });
  var funnelIds = Object.keys(candidatesById);
  Object.keys(auditByCandidate).forEach(function (id) {
    if (funnelIds.indexOf(id) === -1) funnelIds.push(id);
  });
  var funnelCounts = (typeof STAGES !== "undefined" ? STAGES : []).map(function (stage) {
    var count = funnelIds.filter(function (id) { return computeStage(id, candidatesById, auditByCandidate).key === stage.key; }).length;
    return { label: stage.label, count: count };
  });
  var funnelMax = Math.max.apply(null, funnelCounts.map(function (f) { return f.count; }).concat([1]));
  var funnelShades = ["#32145F", "#6525D9", "#8B5CF6", "#C9B3F7"];

  // Hiring Trend -- real application_submitted audit events, bucketed by
  // calendar day over the last 14 days. A day with zero events is a real
  // zero; if there have been NO application_submitted events at all in
  // that window, we say so rather than drawing a misleading flat line.
  var trendDays = recentDayKeys(14);
  var trendCountByDay = {};
  trendDays.forEach(function (d) { trendCountByDay[d] = 0; });
  var trendHasAnyEvent = false;
  extras.auditEntries.forEach(function (e) {
    if (e.action !== "application_submitted" || !e.logged_at) return;
    var day = e.logged_at.slice(0, 10);
    if (trendCountByDay[day] !== undefined) { trendCountByDay[day] += 1; trendHasAnyEvent = true; }
  });

  // Recent Candidates -- real records, newest-ingested first when that
  // timestamp is present (falls back to list order otherwise).
  var recentCandidates = data.candidates.slice().sort(function (a, b) {
    return (b.ingested_at || "").localeCompare(a.ingested_at || "");
  }).slice(0, 5);

  // Open Positions -- published JD records; application count is the
  // number of loaded candidates actually linked to that JD via jd_id
  // (real FK per candidate.schema.json), zero when none are linked yet.
  var openPositions = extras.jds.slice(0, 5).map(function (jd) {
    var count = data.candidates.filter(function (c) { return c.jd_id === jd.jd_id; }).length;
    return { jd: jd, applicationCount: count };
  });

  var visibleCandidates = data.candidates.filter(function (c) {
    return !search.trim() || (c.Name || "").toLowerCase().indexOf(search.trim().toLowerCase()) !== -1;
  });
  if (sort.key) {
    visibleCandidates = visibleCandidates.slice().sort(function (a, b) {
      var av = sortValue(a, sort.key), bv = sortValue(b, sort.key);
      if (av == null) av = "";
      if (bv == null) bv = "";
      if (av < bv) return -1 * sort.dir;
      if (av > bv) return 1 * sort.dir;
      return 0;
    });
  }

  function toggleSort(key) {
    setSort(function (s) {
      if (s.key === key) return { key: key, dir: s.dir * -1 };
      return { key: key, dir: 1 };
    });
  }

  return (
    <div className="view">
      <h2 className="view-heading">{greetingForHour(new Date().getHours())}{firstName ? ", " + firstName : ""} 👋</h2>
      <p className="view-subheading">Here's what's happening with your hiring today.</p>
      <div className="toolbar">
        <p className="muted">
          Showing the snapshot from {meta.lastUpdated ? new Date(meta.lastUpdated).toLocaleString() : "just now"} —
          click Refresh for current data.
        </p>
        <button className="btn" onClick={refreshAll} disabled={data.loading}>{data.loading ? "Refreshing…" : "Refresh"}</button>
      </div>

      {data.error && <div className="banner banner-error">Couldn't load data: {data.error}</div>}

      <div className="stat-row">
        <StatTile label="Total Candidates" value={totalCandidates} tone="info" icon={<IconUsers />} />
        <StatTile label="Fast-Track" value={tierCounts["Recommended for Fast-Track"]} tone="good" icon={<IconPipeline />} />
        <StatTile label="To Be Considered" value={tierCounts["To be Considered"]} tone="warn" icon={<IconQueue />} />
        <StatTile label="Rejected" value={tierCounts["Rejected"]} tone="bad" icon={<IconShield />} />
      </div>

      <div className="dash-grid">
        <div className="dash-card">
          <div className="dash-card-header">
            <h3 className="dash-card-title">Hiring Pipeline</h3>
          </div>
          {totalCandidates === 0 ? (
            <p className="dash-card-empty">No candidates in the pipeline yet.</p>
          ) : (
            <div className="funnel-list">
              {funnelCounts.map(function (f, i) {
                var pct = Math.max(8, Math.round((f.count / funnelMax) * 100));
                return (
                  <div className="funnel-row" key={f.label}>
                    <div className="funnel-row-label">{f.label}</div>
                    <div className="funnel-row-track">
                      <div className="funnel-row-bar" style={{ width: pct + "%", background: funnelShades[i % funnelShades.length] }} />
                    </div>
                    <div className="funnel-row-count">{f.count}</div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div className="dash-card">
          <div className="dash-card-header">
            <h3 className="dash-card-title">Hiring Trend</h3>
            <span className="muted" style={{ fontSize: "0.76rem" }}>Last 14 days</span>
          </div>
          {!trendHasAnyEvent ? (
            <p className="dash-card-empty">No application activity recorded in the last 14 days yet.</p>
          ) : (
            <div className="trend-chart-wrap">
              <ChartCanvas config={{
                type: "line",
                data: {
                  labels: trendDays.map(function (d) { return d.slice(5); }),
                  datasets: [{
                    data: trendDays.map(function (d) { return trendCountByDay[d]; }),
                    borderColor: "#6525D9", backgroundColor: "rgba(101,37,217,0.12)", fill: true,
                    tension: 0.35, pointRadius: 0, pointHoverRadius: 4, borderWidth: 2.5,
                  }],
                },
                options: {
                  responsive: true, maintainAspectRatio: false,
                  plugins: { legend: { display: false } },
                  scales: {
                    x: { grid: { display: false }, ticks: { font: { family: "'Plus Jakarta Sans', sans-serif" } } },
                    y: { beginAtZero: true, ticks: { precision: 0, font: { family: "'Plus Jakarta Sans', sans-serif" } }, grid: { color: "#EAE7F2" } },
                  },
                },
              }} />
            </div>
          )}
        </div>
      </div>

      <div className="dash-grid">
        <div className="dash-card">
          <div className="dash-card-header">
            <h3 className="dash-card-title">Recent Candidates</h3>
          </div>
          {recentCandidates.length === 0 ? (
            <p className="dash-card-empty">No candidates yet.</p>
          ) : (
            <div className="recent-list">
              {recentCandidates.map(function (c) {
                return (
                  <div className="recent-row" key={c.candidate_id}>
                    <div className="avatar-circle">{initialsFor(c.Name)}</div>
                    <div className="recent-row-main">
                      <div className="recent-row-name">{c.Name}</div>
                      <div className="recent-row-sub">{c["Candidate Type"] || "—"}</div>
                    </div>
                    <div className="recent-row-meta">
                      <TierBadge tier={c.ranking_tier} />
                      {c.ingested_at && <span className="recent-row-time">{new Date(c.ingested_at).toLocaleDateString()}</span>}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div className="dash-card">
          <div className="dash-card-header">
            <h3 className="dash-card-title">Open Positions</h3>
            <button className="dash-card-link" onClick={function () { if (props.onNavigate) props.onNavigate("jd-management"); }}>View all →</button>
          </div>
          {extras.jdsError ? (
            <p className="dash-card-empty">{extras.jdsError}</p>
          ) : openPositions.length === 0 ? (
            <p className="dash-card-empty">No published job descriptions yet.</p>
          ) : (
            <div className="position-list">
              {openPositions.map(function (p) {
                return (
                  <div
                    className="position-row"
                    key={p.jd.jd_id}
                    onClick={function () { if (props.onNavigate) props.onNavigate("jd-management"); }}
                  >
                    <div>
                      <div className="position-row-title">{p.jd.title}</div>
                      <div className="position-row-sub">
                        {p.jd.location || "Location not specified"} · {p.applicationCount} application{p.applicationCount === 1 ? "" : "s"}
                      </div>
                    </div>
                    <span className="position-row-arrow">→</span>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {data.observability && (
        <div className="stat-row">
          <StatTile label="LLM Calls" value={data.observability.total_calls} tone="info" icon={<IconPulse />} />
          <StatTile label="Tokens Used" value={data.observability.total_tokens.toLocaleString()} tone="info" icon={<IconCloud />} />
          <StatTile label="Total Cost" value={"$" + data.observability.total_cost_usd.toFixed(4)} tone="warn" icon={<IconKey />} />
        </div>
      )}

      <div className="toolbar">
        <label htmlFor="tier-filter">Filter by tier</label>
        <select id="tier-filter" value={filter} onChange={function (e) { setFilter(e.target.value); }}>
          <option value="">All</option>
          <option value="Recommended for Fast-Track">{tierDisplayLabel("Recommended for Fast-Track")}</option>
          <option value="To be Considered">{tierDisplayLabel("To be Considered")}</option>
          <option value="Rejected">{tierDisplayLabel("Rejected")}</option>
        </select>
        <input
          type="search"
          placeholder="Search by name…"
          value={search}
          onChange={function (e) { setSearch(e.target.value); }}
        />
      </div>

      {data.loading ? (
        <p className="muted">Loading candidates…</p>
      ) : (
        <div className="table-card">
          <table className="candidate-table">
            <thead>
              <tr>
                {SORTABLE_COLUMNS.map(function (col) {
                  var active = sort.key === col.key;
                  return (
                    <th key={col.key}>
                      <button onClick={function () { toggleSort(col.key); }}>
                        {col.label}{active ? (sort.dir === 1 ? " ▲" : " ▼") : ""}
                      </button>
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {visibleCandidates.map(function (c) {
                var isExpanded = expandedId === c.candidate_id;
                return (
                  <React.Fragment key={c.candidate_id}>
                    <tr
                      className="candidate-row-clickable"
                      onClick={function () { setExpandedId(isExpanded ? null : c.candidate_id); }}
                    >
                      <td>
                        <div className="name-cell">
                          <div className="avatar-circle">{initialsFor(c.Name)}</div>
                          {c.Name}
                        </div>
                      </td>
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
        </div>
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
      {props.icon && (
        <div className="stat-tile-top">
          <div className="stat-tile-icon">{props.icon}</div>
        </div>
      )}
      <div className="stat-tile-value">{props.value}</div>
      <div className="stat-tile-label">{props.label}</div>
    </div>
  );
}

function TierBadge(props) {
  var toneClass = props.tier === "Recommended for Fast-Track" ? "badge-good"
    : props.tier === "Rejected" ? "badge-bad" : "badge-neutral";
  // Reads as an AI suggestion a recruiter can accept or override, not a
  // decision already made -- see uiLabels.js's own doc comment.
  return <span className={"badge " + toneClass}>{tierDisplayLabel(props.tier)}</span>;
}
