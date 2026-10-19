/**
 * Recruiter Reports — deliberately a DIFFERENT set of charts from
 * AdminReports.jsx, not the same view with recruiter access bolted on.
 * Admin's Reports are candidate-pool analytics (score distributions, tier
 * breakdown, skills). This is about the recruiter's own operational
 * work: where candidates are stuck in the hiring-stage pipeline, how
 * they're performing at each stage, and the status of this recruiter's
 * bulk-decision proposals (which, per the admin-approval gate, the
 * recruiter can't just push through themselves).
 *
 * Reuses ChartCanvas/ChartCard/CHART_COLORS/PALETTE/countBy from
 * AdminReports.jsx (same global script scope, no bundler/modules here —
 * see frontend/README.md) rather than redefining them.
 */
function RecruiterReports(props) {
  var api = props.api;
  var state = React.useState({ loading: true, error: null, auditEntries: [], stages: [], bulkDecisions: [] });
  var data = state[0];
  var setData = state[1];

  React.useEffect(function () {
    Promise.all([api.listAuditLog(), api.getPipelineConfig(), api.listBulkDecisions()])
      .then(function (results) {
        setData({
          loading: false, error: null,
          auditEntries: results[0].entries, stages: results[1].stages, bulkDecisions: results[2].decisions,
        });
      })
      .catch(function (err) {
        setData({ loading: false, error: err.message, auditEntries: [], stages: [], bulkDecisions: [] });
      });
  }, []);

  if (data.loading) return <div className="view"><h2>Reports</h2><p className="muted">Loading…</p></div>;
  if (data.error) return <div className="view"><h2>Reports</h2><div className="banner banner-error">{data.error}</div></div>;

  var stageUpdates = data.auditEntries.filter(function (e) { return e.action === "stage_update"; });
  var reviewEntries = data.auditEntries.filter(function (e) { return e.action === "human_review_approve" || e.action === "human_review_reject"; });

  // 1. Stage funnel -- how many candidates have EVER reached each
  // configured stage (their current stage is at least this one, could be
  // further along too -- a true funnel, not an exclusive bucket count).
  var reachedByCandidate = {};
  stageUpdates.forEach(function (e) {
    reachedByCandidate[e.candidate_id] = reachedByCandidate[e.candidate_id] || [];
    reachedByCandidate[e.candidate_id].push(e.stage);
  });
  var funnelCounts = data.stages.map(function (stageName) {
    var stageIndex = data.stages.indexOf(stageName);
    var count = 0;
    Object.keys(reachedByCandidate).forEach(function (cid) {
      var reached = reachedByCandidate[cid];
      var furthestIndex = Math.max.apply(null, reached.map(function (s) { return data.stages.indexOf(s); }));
      if (furthestIndex >= stageIndex) count += 1;
    });
    return count;
  });
  var funnelConfig = {
    type: "bar",
    data: { labels: data.stages, datasets: [{ label: "Candidates reached", data: funnelCounts, backgroundColor: CHART_COLORS.blue }] },
    options: { indexAxis: "y", plugins: { legend: { display: false } } },
  };

  // 2. Performance rating distribution across all recorded stage updates
  var perfCounts = countBy(
    stageUpdates.filter(function (e) { return e.performance; }),
    function (e) { return e.performance; },
  );
  var perfOrder = ["Excellent", "Good", "Average", "Poor"];
  var perfLabels = perfOrder.filter(function (p) { return perfCounts[p]; });
  var perfColors = { Excellent: CHART_COLORS.good, Good: CHART_COLORS.blue, Average: CHART_COLORS.neutral, Poor: CHART_COLORS.bad };
  var perfConfig = {
    type: "doughnut",
    data: {
      labels: perfLabels,
      datasets: [{ data: perfLabels.map(function (p) { return perfCounts[p]; }), backgroundColor: perfLabels.map(function (p) { return perfColors[p]; }) }],
    },
  };

  // 3. My bulk-decision proposals, by status
  var bulkCounts = countBy(data.bulkDecisions, function (d) { return d.status; });
  var bulkOrder = ["pending", "approved", "rejected"];
  var bulkLabels = bulkOrder.filter(function (s) { return bulkCounts[s]; });
  var bulkColors = { pending: CHART_COLORS.neutral, approved: CHART_COLORS.good, rejected: CHART_COLORS.bad };
  var bulkConfig = {
    type: "bar",
    data: {
      labels: bulkLabels.map(function (s) { return s.charAt(0).toUpperCase() + s.slice(1); }),
      datasets: [{ label: "Proposals", data: bulkLabels.map(function (s) { return bulkCounts[s]; }), backgroundColor: bulkLabels.map(function (s) { return bulkColors[s]; }) }],
    },
    options: { plugins: { legend: { display: false } } },
  };

  // 4. Individual review decisions (Human Review Queue), approve vs reject
  var reviewApprovals = reviewEntries.filter(function (e) { return e.action === "human_review_approve"; }).length;
  var reviewRejections = reviewEntries.filter(function (e) { return e.action === "human_review_reject"; }).length;
  var reviewConfig = {
    type: "pie",
    data: {
      labels: ["Approved", "Rejected"],
      datasets: [{ data: [reviewApprovals, reviewRejections], backgroundColor: [CHART_COLORS.good, CHART_COLORS.bad] }],
    },
  };

  var noStageData = stageUpdates.length === 0;
  var noBulkData = data.bulkDecisions.length === 0;
  var noReviewData = reviewEntries.length === 0;

  return (
    <div className="view">
      <h2>Reports</h2>
      <p className="muted">
        Your operational view — where candidates sit in the hiring-stage pipeline, how they're performing,
        and the status of your bulk-decision proposals. Candidate-pool analytics (score distributions, skills,
        etc.) are the Admin's separate Reports view.
      </p>
      <div className="chart-grid">
        <ChartCard title="Stage Funnel" wide>
          {noStageData ? <p className="muted">No stages recorded yet.</p> : <ChartCanvas config={funnelConfig} />}
        </ChartCard>
        <ChartCard title="Performance Ratings Recorded">
          {noStageData || perfLabels.length === 0 ? <p className="muted">No performance ratings recorded yet.</p> : <ChartCanvas config={perfConfig} />}
        </ChartCard>
        <ChartCard title="My Bulk-Decision Proposals">
          {noBulkData ? <p className="muted">No bulk proposals yet.</p> : <ChartCanvas config={bulkConfig} />}
        </ChartCard>
        <ChartCard title="Individual Review Decisions">
          {noReviewData ? <p className="muted">No individual reviews recorded yet.</p> : <ChartCanvas config={reviewConfig} />}
        </ChartCard>
      </div>
    </div>
  );
}
