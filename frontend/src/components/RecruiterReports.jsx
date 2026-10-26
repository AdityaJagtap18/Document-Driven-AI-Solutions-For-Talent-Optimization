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
  var snapshot = CDPI_REPORT_SNAPSHOT.useReportSnapshot("recruiter_reports", function () {
    return Promise.all([api.listAuditLog(), api.getPipelineConfig(), api.listBulkDecisions()])
      .then(function (results) {
        return { auditEntries: results[0].entries, stages: results[1].stages, bulkDecisions: results[2].decisions };
      });
  });
  var meta = snapshot.state;
  var data = meta.data || { auditEntries: [], stages: [], bulkDecisions: [] };

  // F-03 (candidate feedback, minimal slice): a separate fetch, and
  // deliberately not fatal to the rest of this page if it fails (e.g.
  // a role without feedback.read) -- the operational pipeline reports
  // above are this page's primary purpose. Same snapshot treatment as
  // everything else on this page.
  var feedbackSnapshot = CDPI_REPORT_SNAPSHOT.useReportSnapshot("recruiter_reports_feedback", function () {
    return api.listCandidateFeedback();
  });
  var feedback = feedbackSnapshot.state.data
    ? { loading: false, error: feedbackSnapshot.state.error, records: feedbackSnapshot.state.data.records, summary: feedbackSnapshot.state.data.summary }
    : { loading: feedbackSnapshot.state.loading, error: feedbackSnapshot.state.error, records: [], summary: null };

  if (meta.loading && !meta.data) return <div className="view"><h2>Reports</h2><p className="muted">Loading…</p></div>;
  if (meta.error && !meta.data) return <div className="view"><h2>Reports</h2><div className="banner banner-error">{meta.error}</div></div>;

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
      <div className="toolbar">
        <p className="muted">
          Your operational view — where candidates sit in the hiring-stage pipeline, how they're performing,
          and the status of your bulk-decision proposals. Candidate-pool analytics (score distributions, skills,
          etc.) are the Admin's separate Reports view. Showing the snapshot from{" "}
          {meta.lastUpdated ? new Date(meta.lastUpdated).toLocaleString() : "just now"} — click Refresh for current data.
        </p>
        <button className="btn" onClick={function () { snapshot.refresh(); feedbackSnapshot.refresh(); }} disabled={meta.loading}>
          {meta.loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>
      {meta.error && meta.data && <div className="banner-error-inline">Refresh failed: {meta.error} (showing the last snapshot)</div>}
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

      <CandidateFeedbackSection feedback={feedback} />
    </div>
  );
}

var FEEDBACK_CATEGORY_LABELS = {
  application_process: "Application Process", communication: "Communication",
  interview_scheduling: "Interview Scheduling", interview_rounds: "Interview Rounds",
  interviewer_interactions: "Interviewer Interactions", transparency: "Transparency",
  overall_experience: "Overall Experience",
};

/**
 * F-03 (candidate feedback, minimal slice). Candidate experience
 * feedback is deliberately shown here, NOT blended into the hiring
 * evaluation charts above -- per the feature's own "keep feedback
 * separate from hiring evaluation" requirement, this section reads
 * from a wholly separate backend container and API call.
 */
function CandidateFeedbackSection(props) {
  var feedback = props.feedback;
  if (feedback.loading) return null;
  if (feedback.error) {
    // Most likely cause: this role doesn't have feedback.read (e.g. a
    // custom role) -- shown quietly, not as a page-breaking error, since
    // the operational reports above are this page's primary content.
    return <p className="muted">Candidate feedback unavailable: {feedback.error}</p>;
  }
  var summary = feedback.summary;
  var records = feedback.records;

  return (
    <React.Fragment>
      <h3 className="subsection-heading">Candidate Experience Feedback</h3>
      <p className="muted">
        Collected after a final hiring decision is recorded -- kept separate from scoring/evaluation data so
        experience feedback never influences hiring decisions.
      </p>
      <div className="stat-row">
        <StatTile label="Requests Sent" value={summary.requested_count} tone="neutral" />
        <StatTile label="Submitted" value={summary.submitted_count} tone="good" />
        <StatTile label="Pending" value={summary.pending_count} tone="neutral" />
        <StatTile label="Response Rate" value={summary.response_rate !== null ? (summary.response_rate * 100).toFixed(1) + "%" : "—"} tone="neutral" />
      </div>
      <div className="table-card">
        <table className="candidate-table">
          <thead><tr><th>Category</th><th>Average Rating (1-5)</th></tr></thead>
          <tbody>
            {Object.keys(FEEDBACK_CATEGORY_LABELS).map(function (c) {
              var avg = summary.category_averages[c];
              return <tr key={c}><td>{FEEDBACK_CATEGORY_LABELS[c]}</td><td>{avg === null || avg === undefined ? "No submissions yet" : avg}</td></tr>;
            })}
          </tbody>
        </table>
      </div>
      <div className="table-card">
        <table className="candidate-table">
          <thead><tr><th>Candidate</th><th>Status</th><th>Requested</th><th>Comment</th></tr></thead>
          <tbody>
            {records.map(function (r) {
              return (
                <tr key={r.id || r.candidate_id}>
                  <td className="muted">{r.candidate_id}</td>
                  <td><span className={"badge " + (r.status === "submitted" ? "badge-good" : "badge-neutral")}>{r.status}</span></td>
                  <td className="muted">{r.requested_at ? new Date(r.requested_at).toLocaleDateString() : "—"}</td>
                  <td>{r.comment || "—"}</td>
                </tr>
              );
            })}
            {records.length === 0 && <tr><td colSpan={4} className="muted">No feedback requests yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </React.Fragment>
  );
}
