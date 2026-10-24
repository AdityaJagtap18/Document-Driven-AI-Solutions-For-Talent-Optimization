/**
 * Admin Reports: 8 charts for reporting/transparency, built from the same
 * api.listCandidates()/api.observabilitySummary() data the Dashboard
 * already uses (not a separate data source) -- so this works identically
 * in mock and real mode, same fidelity caveats as everywhere else in
 * mock mode (see frontend/README.md).
 *
 * Chart.js (CDN, see index.html) draws into plain <canvas> elements via
 * ChartCanvas below -- no react-chartjs-2 wrapper (no npm/bundler here),
 * just the imperative Chart.js API driven from a useEffect.
 *
 * The "Average score by dimension" radar chart is the explainability
 * piece: it's not just "here's the tier breakdown" but "here's WHY
 * Fast-Track candidates and Rejected candidates ended up where they did,
 * dimension by dimension" -- using the raw per-dimension scores
 * (candidate.Scores, see export_real_candidates.py), not just the single
 * final Resume Strength Score. Candidates from a real/Cosmos backend
 * won't have .Scores yet (not in candidate.schema.json) -- missing
 * dimensions are treated as 0 in the average rather than crashing.
 */
var CHART_COLORS = {
  accent: "#6c63f0", good: "#27ad76", bad: "#e2583f", neutral: "#f0974f",
  blue: "#4f8af7", purple: "#8b7af0", teal: "#2ab6a6", amber: "#f0974f",
  gray: "#8c89a6",
};
var PALETTE = [CHART_COLORS.accent, CHART_COLORS.blue, CHART_COLORS.amber, CHART_COLORS.purple,
  CHART_COLORS.teal, CHART_COLORS.bad, CHART_COLORS.gray, CHART_COLORS.good];

function ChartCanvas(props) {
  var canvasRef = React.useRef(null);

  React.useEffect(function () {
    if (!canvasRef.current) return undefined;
    var chart = new Chart(canvasRef.current, props.config);
    return function () { chart.destroy(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(props.config)]);

  return <canvas ref={canvasRef}></canvas>;
}

function ChartCard(props) {
  return (
    <div className={"chart-card" + (props.wide ? " chart-card-wide" : "")}>
      <h4>{props.title}</h4>
      <div className="chart-card-canvas">{props.children}</div>
    </div>
  );
}

/** Half-donut "gauge" with a center total and a legend below — same
 * pattern as the reference dashboard's "Source" card. Chart.js doughnut
 * with circumference/rotation cut down to a semicircle; the center
 * number is a plain positioned <div>, not a canvas plugin, since that's
 * simpler than writing a custom Chart.js plugin for one static label. */
function GaugeCard(props) {
  var config = {
    type: "doughnut",
    data: {
      labels: props.segments.map(function (s) { return s.label; }),
      datasets: [{
        data: props.segments.map(function (s) { return s.value; }),
        backgroundColor: props.segments.map(function (s) { return s.color; }),
        borderWidth: 0,
        borderRadius: 8,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      circumference: 180,
      rotation: 270,
      cutout: "72%",
      layout: { padding: { bottom: 10 } },
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
    },
  };

  return (
    <div className="chart-card gauge-card">
      <h4>{props.title}</h4>
      <div className="gauge-wrap">
        <ChartCanvas config={config} />
        <div className="gauge-center">
          <div className="gauge-center-label">Total</div>
          <div className="gauge-center-value">{props.total}</div>
        </div>
      </div>
      <div className="gauge-legend">
        {props.segments.map(function (s) {
          var pct = props.total ? Math.round((s.value / props.total) * 100) : 0;
          return (
            <div className="gauge-legend-row" key={s.label}>
              <span className="gauge-legend-dot" style={{ background: s.color }}></span>
              <span className="gauge-legend-label">{s.label}</span>
              <span className="gauge-legend-count">{s.value}</span>
              <span className="gauge-legend-pct">{pct}%</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function countBy(items, keyFn) {
  var counts = {};
  items.forEach(function (item) {
    var key = keyFn(item) || "Unspecified";
    counts[key] = (counts[key] || 0) + 1;
  });
  return counts;
}

function avg(nums) {
  var valid = nums.filter(function (n) { return typeof n === "number" && !isNaN(n); });
  if (!valid.length) return 0;
  return valid.reduce(function (a, b) { return a + b; }, 0) / valid.length;
}

function AdminReports(props) {
  var api = props.api;
  var permissions = props.permissions || [];
  var canViewResumeProcessing = permissions.indexOf("observability.read") !== -1;
  var canViewCommsDelivery = permissions.indexOf("comms.delivery.read") !== -1;
  var state = React.useState({ loading: true, error: null, candidates: [], observability: null });
  var data = state[0];
  var setData = state[1];

  React.useEffect(function () {
    Promise.all([api.listCandidates(), api.observabilitySummary()])
      .then(function (results) {
        setData({ loading: false, error: null, candidates: results[0].candidates, observability: results[1] });
      })
      .catch(function (err) {
        setData({ loading: false, error: err.message, candidates: [], observability: null });
      });
  }, []);

  if (data.loading) return <div className="view"><h2>Reports</h2><p className="muted">Loading…</p></div>;
  if (data.error) return <div className="view"><h2>Reports</h2><div className="banner banner-error">{data.error}</div></div>;

  var candidates = data.candidates;

  // 1. Tier distribution (half-donut gauge + legend)
  var tierCounts = countBy(candidates, function (c) { return c.ranking_tier; });
  var tierSegments = [
    { label: "Recommended for Fast-Track", value: tierCounts["Recommended for Fast-Track"] || 0, color: CHART_COLORS.good },
    { label: "To be Considered", value: tierCounts["To be Considered"] || 0, color: CHART_COLORS.neutral },
    { label: "Rejected", value: tierCounts["Rejected"] || 0, color: CHART_COLORS.bad },
  ];

  // 2. Candidate Type distribution (Doughnut)
  var typeCounts = countBy(candidates, function (c) { return c["Candidate Type"]; });
  var typeConfig = {
    type: "doughnut",
    data: { labels: Object.keys(typeCounts), datasets: [{ data: Object.values(typeCounts), backgroundColor: PALETTE }] },
  };

  // 3. Resume Strength Score histogram (Bar)
  var buckets = ["0-2", "2-4", "4-6", "6-8", "8-10"];
  var bucketCounts = [0, 0, 0, 0, 0];
  candidates.forEach(function (c) {
    var s = c["Resume Strength Score"];
    if (typeof s !== "number") return;
    var idx = Math.min(4, Math.floor(s / 2));
    bucketCounts[idx] += 1;
  });
  var scoreHistConfig = {
    type: "bar",
    data: { labels: buckets, datasets: [{ label: "Candidates", data: bucketCounts, backgroundColor: CHART_COLORS.blue }] },
    options: { plugins: { legend: { display: false } } },
  };

  // 4. Normalized Weighted Score by rank (Line)
  var sorted = candidates.slice().sort(function (a, b) {
    return (b.normalized_weighted_score || 0) - (a.normalized_weighted_score || 0);
  });
  var rankLineConfig = {
    type: "line",
    data: {
      labels: sorted.map(function (c, i) { return i + 1; }),
      datasets: [{
        label: "Normalized Weighted Score", data: sorted.map(function (c) { return c.normalized_weighted_score; }),
        borderColor: CHART_COLORS.accent, backgroundColor: "rgba(108,99,240,0.12)", fill: true,
        tension: 0.35, pointRadius: 0, pointHoverRadius: 4, borderWidth: 2.5,
      }],
    },
    options: {
      plugins: { legend: { display: false } },
      scales: { x: { title: { display: true, text: "Rank" }, grid: { display: false } }, y: { grid: { color: "#f0eefa" } } },
    },
  };

  // 5. Top industries (horizontal Bar)
  var industryCounts = countBy(candidates, function (c) { return c["Industry Experience"]; });
  var topIndustries = Object.entries(industryCounts).sort(function (a, b) { return b[1] - a[1]; }).slice(0, 8);
  var industryConfig = {
    type: "bar",
    data: {
      labels: topIndustries.map(function (e) { return e[0]; }),
      datasets: [{ label: "Candidates", data: topIndustries.map(function (e) { return e[1]; }), backgroundColor: CHART_COLORS.teal }],
    },
    options: { indexAxis: "y", plugins: { legend: { display: false } } },
  };

  // 6. Top matching skills (PolarArea)
  var skillCounts = {};
  candidates.forEach(function (c) {
    (c["Top Matching Keywords"] || []).forEach(function (skill) {
      skillCounts[skill] = (skillCounts[skill] || 0) + 1;
    });
  });
  var topSkills = Object.entries(skillCounts).sort(function (a, b) { return b[1] - a[1]; }).slice(0, 8);
  var skillsConfig = {
    type: "polarArea",
    data: {
      labels: topSkills.map(function (e) { return e[0]; }),
      datasets: [{ data: topSkills.map(function (e) { return e[1]; }), backgroundColor: PALETTE }],
    },
  };

  // 7. Average score by dimension, Fast-Track vs Rejected (Radar)
  var dimensions = ["Skill", "Project", "Certificate", "Contribute", "Soft Skill", "Cultural Fit", "Effort", "Experience"];
  function avgByTier(tier) {
    var inTier = candidates.filter(function (c) { return c.ranking_tier === tier; });
    return dimensions.map(function (dim) {
      return avg(inTier.map(function (c) { return c.Scores ? c.Scores[dim] : undefined; }));
    });
  }
  var radarConfig = {
    type: "radar",
    data: {
      labels: dimensions,
      datasets: [
        { label: "Recommended for Fast-Track", data: avgByTier("Recommended for Fast-Track"), borderColor: CHART_COLORS.good, backgroundColor: "rgba(31,111,92,0.2)" },
        { label: "Rejected", data: avgByTier("Rejected"), borderColor: CHART_COLORS.bad, backgroundColor: "rgba(164,64,47,0.2)" },
      ],
    },
  };

  // 8. LLM tokens by call label (Bar)
  var byLabel = (data.observability && data.observability.by_label) || {};
  var labelEntries = Object.entries(byLabel);
  var tokensConfig = {
    type: "bar",
    data: {
      labels: labelEntries.map(function (e) { return e[0]; }),
      datasets: [{ label: "Tokens used", data: labelEntries.map(function (e) { return e[1].tokens; }), backgroundColor: CHART_COLORS.purple }],
    },
    options: { plugins: { legend: { display: false } } },
  };

  return (
    <div className="view">
      <h2>Reports</h2>
      <p className="muted">
        {candidates.length} candidate(s). Built from the same data as the Dashboard — nothing here is
        separately maintained or hand-curated.
      </p>
      <div className="chart-grid">
        <ChartCard title="Normalized Weighted Score by Rank" wide><ChartCanvas config={rankLineConfig} /></ChartCard>
        <GaugeCard title="Tier Distribution" total={candidates.length} segments={tierSegments} />
        <ChartCard title="Candidate Type Distribution"><ChartCanvas config={typeConfig} /></ChartCard>
        <ChartCard title="Resume Strength Score Distribution"><ChartCanvas config={scoreHistConfig} /></ChartCard>
        <ChartCard title="Top Industries"><ChartCanvas config={industryConfig} /></ChartCard>
        <ChartCard title="Top Matching Skills"><ChartCanvas config={skillsConfig} /></ChartCard>
        <ChartCard title="Avg Score by Dimension — Fast-Track vs Rejected"><ChartCanvas config={radarConfig} /></ChartCard>
        <ChartCard title="LLM Tokens Used by Call Type"><ChartCanvas config={tokensConfig} /></ChartCard>
      </div>

      {canViewResumeProcessing && <ResumeProcessingReportCard api={api} />}
      {canViewCommsDelivery && <CommunicationDeliveryReportCard api={api} />}
    </div>
  );
}

/** CDPI Product Personas & Reporting doc, Phase 1 #5 -- real
 * document_extraction outcomes from the AI Observability trace log, not
 * a separately-maintained counter. Shown only to roles holding
 * observability.read (ai_operations_mlops, admin). */
function ResumeProcessingReportCard(props) {
  var api = props.api;
  var state = React.useState({ loading: true, error: null, data: null });
  var data = state[0];
  var setData = state[1];

  React.useEffect(function () {
    api.resumeProcessingReport()
      .then(function (result) { setData({ loading: false, error: null, data: result }); })
      .catch(function (err) { setData({ loading: false, error: err.message, data: null }); });
  }, []);

  if (data.loading) return null;
  if (data.error) return <div className="banner-error-inline">Resume Processing Report: {data.error}</div>;

  return (
    <React.Fragment>
      <h3 className="subsection-heading">Resume Processing Report</h3>
      <p className="muted">Source: {data.data.data_source}</p>
      <div className="integration-card-secrets">
        <span className="badge badge-neutral">{data.data.total_processing_attempts} attempts</span>
        <span className="badge badge-good">{data.data.succeeded} succeeded</span>
        <span className={"badge " + (data.data.failed_count ? "badge-bad" : "badge-neutral")}>{data.data.failed_count} failed</span>
        <span className="badge badge-neutral">
          {data.data.failure_rate === null ? "failure rate: n/a (no attempts)" : "failure rate: " + (data.data.failure_rate * 100).toFixed(1) + "%"}
        </span>
      </div>
      <p className="muted">Pending/queued: not applicable -- this repo's batch pipeline has no queue/backlog concept.</p>
      {!!data.data.recent_failures.length && (
        <div className="table-card">
          <table className="candidate-table">
            <thead><tr><th>Candidate</th><th>Error</th><th>When</th></tr></thead>
            <tbody>
              {data.data.recent_failures.map(function (f, i) {
                return <tr key={i}><td>{f.correlation_id || "—"}</td><td>{f.error}</td><td>{f.logged_at ? new Date(f.logged_at).toLocaleString() : "—"}</td></tr>;
              })}
            </tbody>
          </table>
        </div>
      )}
    </React.Fragment>
  );
}

/** CDPI Product Personas & Reporting doc, Phase 1 #6 -- real delivery
 * records from comms_delivery_log. Shown only to roles holding
 * comms.delivery.read (recruitment_ops_manager-equivalent, admin). */
function CommunicationDeliveryReportCard(props) {
  var api = props.api;
  var state = React.useState({ loading: true, error: null, data: null });
  var data = state[0];
  var setData = state[1];

  React.useEffect(function () {
    api.communicationDeliveryReport()
      .then(function (result) { setData({ loading: false, error: null, data: result }); })
      .catch(function (err) { setData({ loading: false, error: err.message, data: null }); });
  }, []);

  if (data.loading) return null;
  if (data.error) return <div className="banner-error-inline">Communication Delivery Report: {data.error}</div>;

  var channels = Object.keys(data.data.by_channel);

  return (
    <React.Fragment>
      <h3 className="subsection-heading">Communication Delivery Report</h3>
      <p className="muted">{data.data.note}</p>
      {!channels.length && <p className="muted">No delivery records yet.</p>}
      {!!channels.length && (
        <div className="table-card">
          <table className="candidate-table">
            <thead><tr><th>Channel</th><th>Submitted</th><th>Provider accepted</th><th>Failed</th></tr></thead>
            <tbody>
              {channels.map(function (ch) {
                var s = data.data.by_channel[ch];
                return <tr key={ch}><td>{ch}</td><td>{s.submitted}</td><td>{s.provider_accepted}</td><td>{s.failed}</td></tr>;
              })}
            </tbody>
          </table>
        </div>
      )}
    </React.Fragment>
  );
}
