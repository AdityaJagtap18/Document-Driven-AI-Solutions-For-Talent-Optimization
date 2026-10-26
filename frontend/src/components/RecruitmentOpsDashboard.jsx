/**
 * Recruitment Operations Manager dashboard — team-wide pipeline
 * throughput and workload, not one recruiter's own view (RecruiterReports.jsx)
 * or candidate-pool analytics (AdminReports.jsx). Built from the same real
 * audit_log/pipeline-config/bulk-decision data those two pages already
 * chart — no new backend data source for this first slice.
 *
 * Reuses ChartCanvas/ChartCard/CHART_COLORS/countBy from AdminReports.jsx
 * and StatTile from Dashboard.jsx (same global script scope, no bundler —
 * see frontend/README.md).
 */
function RecruitmentOpsDashboard(props) {
  var api = props.api;
  var snapshot = CDPI_REPORT_SNAPSHOT.useReportSnapshot("recruitment_ops_dashboard", function () {
    return Promise.all([api.listAuditLog(), api.getPipelineConfig(), api.listBulkDecisions()])
      .then(function (results) {
        return { auditEntries: results[0].entries, stages: results[1].stages, bulkDecisions: results[2].decisions };
      });
  });
  var meta = snapshot.state;
  var data = meta.data || { auditEntries: [], stages: [], bulkDecisions: [] };

  if (meta.loading && !meta.data) return <div className="view"><h2>Recruitment Operations</h2><p className="muted">Loading…</p></div>;
  if (meta.error && !meta.data) return <div className="view"><h2>Recruitment Operations</h2><div className="banner banner-error">{meta.error}</div></div>;

  var stageUpdates = data.auditEntries.filter(function (e) { return e.action === "stage_update"; });
  var reviewEntries = data.auditEntries.filter(function (e) { return e.action === "human_review_approve" || e.action === "human_review_reject"; });

  // 1. Team pipeline throughput -- same furthest-stage-reached funnel
  // RecruiterReports.jsx charts, since that computation was never scoped
  // to "my" candidates -- it's already the whole team's throughput.
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

  // 2. Workload distribution -- stage updates recorded, by whoever
  // recorded them (audit_log's real `updated_by` field, not a guess).
  var workloadCounts = countBy(
    stageUpdates.filter(function (e) { return e.updated_by; }),
    function (e) { return e.updated_by; },
  );
  var workloadNames = Object.keys(workloadCounts).sort(function (a, b) { return workloadCounts[b] - workloadCounts[a]; });
  var workloadConfig = {
    type: "bar",
    data: {
      labels: workloadNames,
      datasets: [{ label: "Stage updates recorded", data: workloadNames.map(function (n) { return workloadCounts[n]; }), backgroundColor: PALETTE }],
    },
    options: { indexAxis: "y", plugins: { legend: { display: false } } },
  };

  // 3. Review decisions, by reviewer -- the real `reviewer` field on
  // human_review_approve/reject audit entries.
  var reviewerCounts = countBy(
    reviewEntries.filter(function (e) { return e.reviewer; }),
    function (e) { return e.reviewer; },
  );
  var reviewerNames = Object.keys(reviewerCounts).sort(function (a, b) { return reviewerCounts[b] - reviewerCounts[a]; });
  var reviewerConfig = {
    type: "bar",
    data: {
      labels: reviewerNames,
      datasets: [{ label: "Decisions recorded", data: reviewerNames.map(function (n) { return reviewerCounts[n]; }), backgroundColor: PALETTE }],
    },
    options: { indexAxis: "y", plugins: { legend: { display: false } } },
  };

  // 4. Operational bottleneck -- bulk-decision proposals still awaiting
  // an admin's approval (the real pending-approval gate, see Pipeline.jsx's
  // BulkApprovalsPanel). Not actionable from here (that approval step is
  // Admin-only) -- this is visibility into a real backlog, not a new
  // approval surface.
  var pendingBulk = data.bulkDecisions.filter(function (d) { return d.status === "pending"; });
  var pendingCandidateCount = pendingBulk.reduce(function (sum, d) { return sum + (d.candidate_ids || []).length; }, 0);

  var noStageData = stageUpdates.length === 0;
  var noWorkloadData = workloadNames.length === 0;
  var noReviewerData = reviewerNames.length === 0;

  return (
    <div className="view">
      <h2>Recruitment Operations</h2>
      <div className="toolbar">
        <p className="muted">
          Team-wide pipeline throughput and workload — where the whole recruiting team's candidates are stuck,
          who's recording the work, and what's backlogged waiting on an admin's bulk-approval sign-off.
          Showing the snapshot from {meta.lastUpdated ? new Date(meta.lastUpdated).toLocaleString() : "just now"} —
          click Refresh for current data.
        </p>
        <button className="btn" onClick={snapshot.refresh} disabled={meta.loading}>{meta.loading ? "Refreshing…" : "Refresh"}</button>
      </div>
      {meta.error && meta.data && <div className="banner-error-inline">Refresh failed: {meta.error} (showing the last snapshot)</div>}

      <div className="stat-row">
        <StatTile label="Pending Bulk Proposals" value={pendingBulk.length} tone={pendingBulk.length > 0 ? "neutral" : "good"} />
        <StatTile label="Candidates in Pending Proposals" value={pendingCandidateCount} tone={pendingCandidateCount > 0 ? "neutral" : "good"} />
        <StatTile label="Review Decisions Recorded" value={reviewEntries.length} tone="neutral" />
        <StatTile label="Stage Updates Recorded" value={stageUpdates.length} tone="neutral" />
      </div>

      <div className="chart-grid">
        <ChartCard title="Team Pipeline Throughput" wide>
          {noStageData ? <p className="muted">No stages recorded yet.</p> : <ChartCanvas config={funnelConfig} />}
        </ChartCard>
        <ChartCard title="Stage-Update Workload by Team Member">
          {noWorkloadData ? <p className="muted">No stage updates recorded yet.</p> : <ChartCanvas config={workloadConfig} />}
        </ChartCard>
        <ChartCard title="Review Decisions by Reviewer">
          {noReviewerData ? <p className="muted">No review decisions recorded yet.</p> : <ChartCanvas config={reviewerConfig} />}
        </ChartCard>
      </div>
    </div>
  );
}
