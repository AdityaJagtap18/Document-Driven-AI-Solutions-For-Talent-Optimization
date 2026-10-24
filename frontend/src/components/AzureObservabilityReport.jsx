/**
 * Admin Panel: Service Observability Report -- covers all of CDPI's real,
 * discovered subsystems (not just Azure/AI), reusing every existing real
 * endpoint rather than re-implementing them:
 *  - Real Azure Monitor / Log Analytics QUERY-BACK (azure_monitor_query.py,
 *    manage/observability-report/*) -- the one genuinely NEW telemetry
 *    source this feature adds; everything else below already existed.
 *  - Per-service Azure connectivity/health -> manage/azure-readiness (same
 *    data Azure Readiness' Services tab shows; reused here as a compact
 *    matrix, not duplicated logic).
 *  - Document/resume processing -> manage/reports/resume-processing (real
 *    document_extraction spans, see that endpoint's own docstring).
 *  - External integrations (WhatsApp/email) ->
 *    manage/reports/communication-delivery (real comms_delivery_log
 *    records; email has NO delivery logging at all today -- a real,
 *    stated gap, not a zero-volume coincidence, see that endpoint).
 *  - Local LLM/trace token-cost-latency detail + the full Trace Explorer ->
 *    AI Observability Center (link out, not duplicated).
 *  - Real Azure resource cost -> Azure Readiness' Cost & Usage tab (link out).
 *
 * Every number is labeled with its real source. `overall_status` is
 * computed server-side from real ingestion evidence, never just "the page
 * loaded" or "a connection string is set". One section failing to load
 * (e.g. a role lacking comms.delivery.read) never blanks the rest of the
 * page -- each section fetches and fails independently.
 *
 * CUSTOMIZATION: which sections render is user-controlled (Customize panel
 * below) and persisted to localStorage ONLY -- this is a per-browser,
 * per-device convenience, explicitly NOT a server-persisted/per-account
 * preference (no backend dashboard-config model exists in this repo; see
 * docs/AZURE_OBSERVABILITY.md for the scoping decision to skip full
 * drag-and-drop/resize/named-layouts given this app's scale). Hidden
 * widgets are still fetched as "available" if their data loaded -- hiding
 * is a display preference, not a fetch skip, so toggling one back on is
 * instant.
 */
var OBS_REPORT_WIDGETS = [
  { id: "kpis", label: "Executive KPIs" },
  { id: "request_volume", label: "Request Volume (Azure Monitor)" },
  { id: "ai_usage", label: "AI Model Usage (Azure Monitor)" },
  { id: "ai_usage_chart", label: "AI Token Usage by Model (chart)" },
  { id: "recent_errors", label: "Error Analysis (Azure Monitor)" },
  { id: "service_health", label: "Azure Service Health Matrix" },
  { id: "service_health_chart", label: "Service Health Breakdown (chart)" },
  { id: "workflow_breakdown", label: "Traces by Workflow: Succeeded vs Failed (chart)" },
  { id: "workflow_latency", label: "Average Duration by Workflow (chart)" },
  { id: "document_processing", label: "Document / Resume Processing" },
  { id: "external_integrations", label: "External Integrations (WhatsApp / Email)" },
  { id: "local_recap", label: "Local Observability Recap" },
];
var OBS_REPORT_WIDGETS_STORAGE_KEY = "cdpi_observability_report_hidden_widgets";

function _loadHiddenWidgets() {
  try {
    var raw = window.localStorage.getItem(OBS_REPORT_WIDGETS_STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch (e) { return []; }
}
function _saveHiddenWidgets(ids) {
  try { window.localStorage.setItem(OBS_REPORT_WIDGETS_STORAGE_KEY, JSON.stringify(ids)); } catch (e) { /* per-device convenience only; ignore quota/availability errors */ }
}

function AzureObservabilityReport(props) {
  var api = props.api;
  var hoursState = React.useState(24);
  var hours = hoursState[0];
  var setHours = hoursState[1];

  var summaryState = React.useState(null);
  var summary = summaryState[0];
  var setSummary = summaryState[1];
  var telemetryState = React.useState(null);
  var telemetry = telemetryState[0];
  var setTelemetry = telemetryState[1];
  var loadingState = React.useState(true);
  var loading = loadingState[0];
  var setLoading = loadingState[1];
  var errorState = React.useState(null);
  var error = errorState[0];
  var setError = errorState[1];
  var exportingState = React.useState(false);
  var exporting = exportingState[0];
  var setExporting = exportingState[1];

  var serviceHealthState = React.useState(null);
  var serviceHealth = serviceHealthState[0];
  var setServiceHealth = serviceHealthState[1];
  var serviceHealthErrorState = React.useState(null);
  var serviceHealthError = serviceHealthErrorState[0];
  var setServiceHealthError = serviceHealthErrorState[1];

  var docProcessingState = React.useState(null);
  var docProcessing = docProcessingState[0];
  var setDocProcessing = docProcessingState[1];
  var docProcessingErrorState = React.useState(null);
  var docProcessingError = docProcessingErrorState[0];
  var setDocProcessingError = docProcessingErrorState[1];

  var commsState = React.useState(null);
  var comms = commsState[0];
  var setComms = commsState[1];
  var commsErrorState = React.useState(null);
  var commsError = commsErrorState[0];
  var setCommsError = commsErrorState[1];

  var customizingState = React.useState(false);
  var customizing = customizingState[0];
  var setCustomizing = customizingState[1];
  var hiddenWidgetsState = React.useState(_loadHiddenWidgets);
  var hiddenWidgets = hiddenWidgetsState[0];
  var setHiddenWidgets = hiddenWidgetsState[1];

  function isVisible(widgetId) { return hiddenWidgets.indexOf(widgetId) === -1; }
  function toggleWidget(widgetId) {
    var next = isVisible(widgetId) ? hiddenWidgets.concat([widgetId]) : hiddenWidgets.filter(function (id) { return id !== widgetId; });
    setHiddenWidgets(next);
    _saveHiddenWidgets(next);
  }
  function resetWidgets() {
    setHiddenWidgets([]);
    _saveHiddenWidgets([]);
  }

  function load(bypassCache) {
    if (bypassCache) {
      window.CDPI_AZURE_CACHE.invalidate("obs-report:summary:" + hours);
      window.CDPI_AZURE_CACHE.invalidate("obs-report:telemetry:" + hours);
      window.CDPI_AZURE_CACHE.invalidatePrefix("azure-readiness:");
      window.CDPI_AZURE_CACHE.invalidate("obs-report:doc-processing");
      window.CDPI_AZURE_CACHE.invalidate("obs-report:comms-delivery");
    }
    setLoading(true);
    setError(null);
    Promise.all([
      window.CDPI_AZURE_CACHE.get("obs-report:summary:" + hours, function () { return api.getObservabilityReportSummary(hours); }),
      window.CDPI_AZURE_CACHE.get("obs-report:telemetry:" + hours, function () { return api.getObservabilityReportTelemetry(hours, 20); }),
    ])
      .then(function (results) {
        setSummary(results[0]);
        setTelemetry(results[1]);
      })
      .catch(function (err) { setError(err.message); })
      .then(function () { setLoading(false); });

    // Each of these fails independently -- a 403 (role lacks the
    // permission) or any other error only blanks its own section,
    // never the rest of the page.
    window.CDPI_AZURE_CACHE.get("azure-readiness:list", function () { return api.getAzureReadiness(); })
      .then(setServiceHealth).catch(function (err) { setServiceHealthError(err.message); });
    window.CDPI_AZURE_CACHE.get("obs-report:doc-processing", function () { return api.resumeProcessingReport(); })
      .then(setDocProcessing).catch(function (err) { setDocProcessingError(err.message); });
    window.CDPI_AZURE_CACHE.get("obs-report:comms-delivery", function () { return api.communicationDeliveryReport(); })
      .then(setComms).catch(function (err) { setCommsError(err.message); });
  }

  React.useEffect(function () { load(false); }, [hours]);

  function doExport() {
    setExporting(true);
    api.exportObservabilityReportCsv(hours)
      .then(function (csvText) {
        var blob = new Blob([csvText], { type: "text/csv" });
        var url = URL.createObjectURL(blob);
        var a = document.createElement("a");
        a.href = url;
        a.download = "azure_observability_request_volume_" + hours + "h.csv";
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
      })
      .catch(function (err) { window.alert("Export failed: " + err.message); })
      .then(function () { setExporting(false); });
  }

  return (
    <div className="view" data-testid="azure-observability-report-page">
      <h2>Service Observability Report</h2>
      <p className="muted">
        Real telemetry across CDPI's actual subsystems -- Azure Monitor-ingested metrics, per-service Azure
        connectivity, document/resume processing, and external-integration delivery -- composed from this app's
        existing real endpoints, not re-implemented. See the <strong>AI Observability Center</strong> for full
        local trace detail, and <strong>Azure Readiness</strong> for live per-service checks and real cost.
      </p>

      <div className="toolbar">
        <label className="login-field" style={{ display: "inline-block", width: "auto", marginRight: 12 }}>
          Time range
          <select value={hours} onChange={function (e) { setHours(Number(e.target.value)); }}>
            <option value={1}>Last 1 hour</option>
            <option value={6}>Last 6 hours</option>
            <option value={24}>Last 24 hours</option>
            <option value={72}>Last 3 days</option>
            <option value={168}>Last 7 days</option>
          </select>
        </label>
        <button className="btn" onClick={function () { load(true); }} disabled={loading}>{loading ? "Refreshing…" : "Refresh"}</button>
        <button className="btn" onClick={doExport} disabled={exporting || loading}>{exporting ? "Exporting…" : "Export CSV"}</button>
        <button className={"btn" + (customizing ? " btn-primary" : "")} onClick={function () { setCustomizing(!customizing); }}>
          {customizing ? "Done customizing" : "Customize"}
        </button>
      </div>

      {customizing && (
        <div className="chart-card" style={{ marginBottom: 16 }}>
          <h4>Customize this dashboard</h4>
          <p className="muted" style={{ fontSize: "0.8rem" }}>
            Which sections to show -- saved in this browser only (not a server-side, per-account preference;
            see docs/AZURE_OBSERVABILITY.md).
          </p>
          {OBS_REPORT_WIDGETS.map(function (w) {
            return (
              <label key={w.id} style={{ display: "block", margin: "6px 0" }}>
                <input type="checkbox" checked={isVisible(w.id)} onChange={function () { toggleWidget(w.id); }} />{" "}
                {w.label}
              </label>
            );
          })}
          <button className="btn" onClick={resetWidgets} style={{ marginTop: 8 }}>Restore default (show all)</button>
        </div>
      )}

      {error && <div className="banner banner-error">{error}</div>}
      {loading && !summary && <p className="muted">Loading…</p>}

      {summary && (
        <React.Fragment>
          <ReportHeader summary={summary} hours={hours} />
          {isVisible("kpis") && <KpiRow summary={summary} />}
          <div className="chart-grid">
            {isVisible("request_volume") && <RequestVolumeChart telemetry={telemetry} />}
            {isVisible("ai_usage") && <AiUsageTable telemetry={telemetry} />}
            {isVisible("ai_usage_chart") && <AiUsageChart telemetry={telemetry} />}
            {isVisible("service_health_chart") && <ServiceHealthChart data={serviceHealth} error={serviceHealthError} />}
            {isVisible("workflow_breakdown") && <WorkflowBreakdownChart summary={summary} />}
            {isVisible("workflow_latency") && <WorkflowLatencyChart summary={summary} />}
          </div>
          {isVisible("recent_errors") && <RecentErrorsTable telemetry={telemetry} />}
          {isVisible("service_health") && <ServiceHealthMatrix data={serviceHealth} error={serviceHealthError} />}
          {isVisible("document_processing") && <DocumentProcessingSection data={docProcessing} error={docProcessingError} />}
          {isVisible("external_integrations") && <ExternalIntegrationsSection data={comms} error={commsError} />}
          {isVisible("local_recap") && <LocalObservabilityRecap summary={summary} />}
        </React.Fragment>
      )}
    </div>
  );
}

var OBS_REPORT_STATUS_BADGE = {
  HEALTHY: "badge-good", DEGRADED: "badge-neutral",
  CONFIGURED_NO_INGESTION: "badge-neutral", NOT_CONFIGURED: "badge-bad", UNKNOWN: "badge-neutral",
};

function ReportHeader(props) {
  var s = props.summary;
  return (
    <div className="chart-card" style={{ marginBottom: 16 }}>
      <h4>
        Overall status: <span className={"badge " + (OBS_REPORT_STATUS_BADGE[s.overall_status] || "badge-neutral")}>{s.overall_status}</span>
      </h4>
      <p className="muted" style={{ fontSize: "0.85rem" }}>
        Reporting period: last {props.hours}h &middot; Generated: {new Date(s.generated_at).toLocaleString()} &middot;{" "}
        Data source: Azure Monitor (Log Analytics) + local JSONL &middot; Environment: this app's configured Azure resource group
      </p>
      <p className="muted" style={{ fontSize: "0.85rem" }}>
        Last successful Azure telemetry ingestion:{" "}
        {s.telemetry_health.last_ingested_at
          ? new Date(s.telemetry_health.last_ingested_at).toLocaleString()
          : <em>none confirmed -- {s.telemetry_health.detail}</em>}
      </p>
    </div>
  );
}

function Kpi(props) {
  var displayValue = (props.value === null || props.value === undefined) ? "—" : props.value;
  return (
    <div className="chart-card" style={{ textAlign: "center" }}>
      <div style={{ fontSize: "1.6rem", fontWeight: 700 }}>{displayValue}</div>
      <div className="muted" style={{ fontSize: "0.8rem" }}>{props.label}</div>
      {props.unavailable && <div className="muted" style={{ fontSize: "0.7rem", marginTop: 4 }}>Unavailable: {props.unavailable}</div>}
    </div>
  );
}

function KpiRow(props) {
  var at = props.summary.azure_telemetry;
  var reason = at.unavailable_reason;
  return (
    <div className="chart-grid" style={{ marginBottom: 16 }}>
      <Kpi label="Total requests (Azure-ingested)" value={at.total_requests} unavailable={at.total_requests === null ? reason : null} />
      <Kpi label="Success rate" value={at.success_rate !== null ? (Math.round(at.success_rate * 10000) / 100) + "%" : null} unavailable={at.success_rate === null ? reason : null} />
      <Kpi label="Failure rate" value={at.failure_rate !== null ? (Math.round(at.failure_rate * 10000) / 100) + "%" : null} unavailable={at.failure_rate === null ? reason : null} />
      <Kpi label="p95 latency (ms)" value={at.latency.available ? at.latency.p95 : null}
           unavailable={!at.latency.available ? at.latency.detail : (at.latency.p95 === null ? at.latency.detail : null)} />
      <Kpi label="AI inference requests" value={at.ai_requests} unavailable={at.ai_requests === null ? reason : null} />
      <Kpi label="AI token usage" value={at.ai_total_tokens} unavailable={at.ai_total_tokens === null ? reason : null} />
      <Kpi label="Estimated AI cost (USD)" value={at.ai_total_cost_usd !== null ? ("$" + at.ai_total_cost_usd) : null} unavailable={at.ai_total_cost_usd === null ? reason : null} />
      <Kpi label="Azure telemetry configured" value={at.configured ? "Yes" : "No"} />
    </div>
  );
}

function RequestVolumeChart(props) {
  var t = props.telemetry;
  if (!t) return null;
  var rv = t.request_volume;
  if (!rv.available) {
    return <ChartCard title="Request Volume Over Time"><p className="muted">Unavailable — {rv.detail}</p></ChartCard>;
  }
  if (rv.buckets.length === 0) {
    return <ChartCard title="Request Volume Over Time"><p className="muted">No requests recorded in this window.</p></ChartCard>;
  }
  var config = {
    type: "line",
    data: {
      labels: rv.buckets.map(function (b) { return new Date(b.timestamp).toLocaleString(); }),
      datasets: [
        { label: "Succeeded", data: rv.buckets.map(function (b) { return b.succeeded; }), borderColor: CHART_COLORS.good, backgroundColor: CHART_COLORS.good, tension: 0.2 },
        { label: "Failed", data: rv.buckets.map(function (b) { return b.failed; }), borderColor: CHART_COLORS.bad, backgroundColor: CHART_COLORS.bad, tension: 0.2 },
      ],
    },
    options: { responsive: true, maintainAspectRatio: false, scales: { y: { beginAtZero: true } } },
  };
  return (
    <ChartCard title="Request Volume Over Time" wide>
      <ChartCanvas config={config} />
    </ChartCard>
  );
}

function AiUsageTable(props) {
  var t = props.telemetry;
  if (!t) return null;
  var usage = t.ai_usage;
  return (
    <div className="chart-card">
      <h4>AI Model Usage</h4>
      {!usage.available && <p className="muted">Unavailable — {usage.detail}</p>}
      {usage.available && usage.by_model.length === 0 && <p className="muted">No AI calls recorded in this window.</p>}
      {usage.available && usage.by_model.length > 0 && (
        <table className="table">
          <thead><tr><th>Model</th><th>Provider</th><th>Calls</th><th>Failed</th><th>Tokens</th><th>Cost (USD)</th></tr></thead>
          <tbody>
            {usage.by_model.map(function (m, i) {
              return (
                <tr key={i}>
                  <td>{m.model || "—"}</td>
                  <td>{m.provider || "—"}</td>
                  <td>{m.calls}</td>
                  <td>{m.failed}</td>
                  <td>{m.total_tokens !== null ? m.total_tokens : "—"}</td>
                  <td>{m.total_cost_usd !== null ? ("$" + m.total_cost_usd) : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

/** Bar chart of real token usage by model -- same ai_usage data AiUsageTable
 * shows as a table, just visualized, so a reader can see at a glance which
 * model dominates usage without reading rows. */
function AiUsageChart(props) {
  var t = props.telemetry;
  if (!t) return null;
  var usage = t.ai_usage;
  if (!usage.available) {
    return <ChartCard title="AI Token Usage by Model"><p className="muted">Unavailable — {usage.detail}</p></ChartCard>;
  }
  if (usage.by_model.length === 0) {
    return <ChartCard title="AI Token Usage by Model"><p className="muted">No AI calls recorded in this window.</p></ChartCard>;
  }
  var config = {
    type: "bar",
    data: {
      labels: usage.by_model.map(function (m) { return (m.model || "unknown") + (m.provider ? " (" + m.provider + ")" : ""); }),
      datasets: [{ label: "Tokens", data: usage.by_model.map(function (m) { return m.total_tokens || 0; }), backgroundColor: PALETTE }],
    },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true } } },
  };
  return <ChartCard title="AI Token Usage by Model"><ChartCanvas config={config} /></ChartCard>;
}

var SERVICE_HEALTH_CHART_COLORS = {
  READY: CHART_COLORS.good, PARTIALLY_VERIFIED: CHART_COLORS.amber, DEGRADED: CHART_COLORS.amber,
  NOT_CONFIGURED: CHART_COLORS.bad, AUTHENTICATION_REQUIRED: CHART_COLORS.bad, PERMISSION_DENIED: CHART_COLORS.bad,
  CONNECTION_FAILED: CHART_COLORS.bad, NOT_INTEGRATED: CHART_COLORS.gray, UNKNOWN: CHART_COLORS.gray,
};

/** Donut of real Azure service counts by readiness status -- a reader can
 * see at a glance "how many of our Azure integrations are actually READY"
 * without reading the full matrix table below it. A service with UNKNOWN
 * health is its own distinct slice, never folded into "healthy". */
function ServiceHealthChart(props) {
  if (props.error) {
    return <ChartCard title="Service Health Breakdown"><p className="muted">Unavailable — {props.error}</p></ChartCard>;
  }
  if (!props.data) {
    return <ChartCard title="Service Health Breakdown"><p className="muted">Loading…</p></ChartCard>;
  }
  var counts = {};
  (props.data.services || []).forEach(function (s) { counts[s.status] = (counts[s.status] || 0) + 1; });
  var statuses = Object.keys(counts);
  if (statuses.length === 0) {
    return <ChartCard title="Service Health Breakdown"><p className="muted">No Azure services registered.</p></ChartCard>;
  }
  var config = {
    type: "doughnut",
    data: {
      labels: statuses,
      datasets: [{ data: statuses.map(function (s) { return counts[s]; }), backgroundColor: statuses.map(function (s) { return SERVICE_HEALTH_CHART_COLORS[s] || CHART_COLORS.gray; }), borderWidth: 0 }],
    },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "bottom" } } },
  };
  return <ChartCard title="Service Health Breakdown"><ChartCanvas config={config} /></ChartCard>;
}

/** Stacked bar: real succeeded-vs-failed span counts per workflow, sourced
 * from local_observability.ai_traces.by_workflow -- this data (7 real
 * instrumented workflows: resume parsing, resume matching, LLM calls,
 * Azure config checks, Azure ephemeral tests, Azure AI Foundry test
 * requests, observability self-tests) existed in the API response already
 * but had no chart at all before this -- a real gap this adds a chart for. */
function WorkflowBreakdownChart(props) {
  var byWorkflow = props.summary.local_observability.ai_traces.by_workflow || {};
  var workflows = Object.keys(byWorkflow);
  if (workflows.length === 0) {
    return <ChartCard title="Traces by Workflow"><p className="muted">No traces recorded locally.</p></ChartCard>;
  }
  var config = {
    type: "bar",
    data: {
      labels: workflows,
      datasets: [
        { label: "Succeeded", data: workflows.map(function (w) { return byWorkflow[w].succeeded; }), backgroundColor: CHART_COLORS.good },
        { label: "Failed", data: workflows.map(function (w) { return byWorkflow[w].failed; }), backgroundColor: CHART_COLORS.bad },
      ],
    },
    options: { responsive: true, maintainAspectRatio: false, scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true } } },
  };
  return <ChartCard title="Traces by Workflow: Succeeded vs Failed" wide><ChartCanvas config={config} /></ChartCard>;
}

/** Bar chart of real average span duration per workflow -- same
 * by_workflow data as above, a different cut (where is time actually
 * going, not just pass/fail). */
function WorkflowLatencyChart(props) {
  var byWorkflow = props.summary.local_observability.ai_traces.by_workflow || {};
  var workflows = Object.keys(byWorkflow);
  if (workflows.length === 0) {
    return <ChartCard title="Average Duration by Workflow"><p className="muted">No traces recorded locally.</p></ChartCard>;
  }
  var config = {
    type: "bar",
    data: {
      labels: workflows,
      datasets: [{ label: "Avg duration (ms)", data: workflows.map(function (w) { return byWorkflow[w].avg_duration_ms || 0; }), backgroundColor: CHART_COLORS.blue }],
    },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true } } },
  };
  return <ChartCard title="Average Duration by Workflow (ms)"><ChartCanvas config={config} /></ChartCard>;
}

function RecentErrorsTable(props) {
  var t = props.telemetry;
  if (!t) return null;
  var errs = t.recent_errors;
  return (
    <div className="chart-card" style={{ marginTop: 16 }}>
      <h4>Error Analysis — Recent Failures (Azure-ingested)</h4>
      {!errs.available && <p className="muted">Unavailable — {errs.detail}</p>}
      {errs.available && errs.errors.length === 0 && <p className="muted">No failures found in this window.</p>}
      {errs.available && errs.errors.length > 0 && (
        <table className="table">
          <thead><tr><th>Time</th><th>Source</th><th>Workflow / Label</th><th>Stage</th><th>Error</th><th>Correlation ID</th></tr></thead>
          <tbody>
            {errs.errors.map(function (e, i) {
              return (
                <tr key={i}>
                  <td>{new Date(e.timestamp).toLocaleString()}</td>
                  <td>{e.source}</td>
                  <td>{e.workflow || e.label || "—"}</td>
                  <td>{e.stage || "—"}</td>
                  <td style={{ maxWidth: 320, overflowWrap: "break-word" }}>{e.error || "—"}</td>
                  <td><code style={{ fontSize: "0.75rem" }}>{e.correlation_id || "—"}</code></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

function LocalObservabilityRecap(props) {
  var lo = props.summary.local_observability;
  return (
    <div className="chart-card" style={{ marginTop: 16 }}>
      <h4>Local Observability (this process's JSONL logs)</h4>
      <p className="muted" style={{ fontSize: "0.8rem" }}>{lo.note}</p>
      <p>
        {lo.llm_calls.total_calls} LLM call(s) logged locally, {lo.llm_calls.total_tokens} token(s),
        ${lo.llm_calls.total_cost_usd} estimated cost. Full detail, per-trace spans, and the Trace Explorer
        live on the <strong>AI Observability Center</strong> page.
      </p>
    </div>
  );
}

var SERVICE_HEALTH_BADGE = {
  READY: "badge-good", PARTIALLY_VERIFIED: "badge-neutral", DEGRADED: "badge-neutral",
  NOT_CONFIGURED: "badge-bad", AUTHENTICATION_REQUIRED: "badge-bad", PERMISSION_DENIED: "badge-bad",
  CONNECTION_FAILED: "badge-bad", NOT_INTEGRATED: "badge-neutral", UNKNOWN: "badge-neutral",
};

/** Compact matrix reusing manage/azure-readiness (the same real data
 * Azure Readiness' Services tab shows, computed from already-cached
 * checks -- this does not trigger a new live check). A 403 here (role
 * lacks azure.config.read) is shown as "no permission", not an error
 * that blanks the rest of the page. */
function ServiceHealthMatrix(props) {
  if (props.error) {
    return <div className="chart-card" style={{ marginTop: 16 }}><h4>Azure Service Health Matrix</h4><p className="muted">Unavailable — {props.error}</p></div>;
  }
  if (!props.data) {
    return <div className="chart-card" style={{ marginTop: 16 }}><h4>Azure Service Health Matrix</h4><p className="muted">Loading…</p></div>;
  }
  var services = props.data.services || [];
  return (
    <div className="chart-card" style={{ marginTop: 16 }}>
      <h4>Azure Service Health Matrix</h4>
      <p className="muted" style={{ fontSize: "0.8rem" }}>
        Composed status per Azure integration (configuration, authentication, connectivity) — a successful
        application health check never implies every dependency below is healthy. See <strong>Azure Readiness</strong> to
        run a live check.
      </p>
      <table className="table">
        <thead><tr><th>Service</th><th>Status</th><th>Reason</th></tr></thead>
        <tbody>
          {services.map(function (s, i) {
            return (
              <tr key={i}>
                <td>{s.service_name}</td>
                <td><span className={"badge " + (SERVICE_HEALTH_BADGE[s.status] || "badge-neutral")}>{s.status}</span></td>
                <td style={{ fontSize: "0.85rem" }}>{s.reason || "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Reuses manage/reports/resume-processing -- real document_extraction
 * spans, not a separately-maintained counter. pending_queue_depth is
 * honestly null (this repo's batch pipeline has no queue concept), never
 * invented. */
function DocumentProcessingSection(props) {
  if (props.error) {
    return <div className="chart-card" style={{ marginTop: 16 }}><h4>Document / Resume Processing</h4><p className="muted">Unavailable — {props.error}</p></div>;
  }
  if (!props.data) {
    return <div className="chart-card" style={{ marginTop: 16 }}><h4>Document / Resume Processing</h4><p className="muted">Loading…</p></div>;
  }
  var d = props.data;
  return (
    <div className="chart-card" style={{ marginTop: 16 }}>
      <h4>Document / Resume Processing</h4>
      <p className="muted" style={{ fontSize: "0.8rem" }}>Source: {d.data_source}</p>
      <div className="stat-row">
        <div className="stat-tile stat-tile-neutral"><div className="stat-tile-value">{d.total_processing_attempts}</div><div className="stat-tile-label">Processing attempts</div></div>
        <div className="stat-tile stat-tile-good"><div className="stat-tile-value">{d.succeeded}</div><div className="stat-tile-label">Succeeded</div></div>
        <div className="stat-tile stat-tile-bad"><div className="stat-tile-value">{d.failed_count}</div><div className="stat-tile-label">Failed</div></div>
        <div className="stat-tile stat-tile-neutral"><div className="stat-tile-value">{d.failure_rate !== null ? (Math.round(d.failure_rate * 10000) / 100) + "%" : "—"}</div><div className="stat-tile-label">Failure rate</div></div>
        <div className="stat-tile stat-tile-neutral"><div className="stat-tile-value">{d.pending_queue_depth === null ? "N/A" : d.pending_queue_depth}</div><div className="stat-tile-label">Pending queue depth</div></div>
      </div>
      {d.recent_failures && d.recent_failures.length > 0 && (
        <table className="table">
          <thead><tr><th>Logged at</th><th>Correlation ID</th><th>Error</th></tr></thead>
          <tbody>
            {d.recent_failures.map(function (f, i) {
              return <tr key={i}><td>{f.logged_at ? new Date(f.logged_at).toLocaleString() : "—"}</td><td><code style={{ fontSize: "0.75rem" }}>{f.correlation_id || "—"}</code></td><td>{f.error || "—"}</td></tr>;
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

/** Reuses manage/reports/communication-delivery -- real
 * comms_delivery_log records. Email has NO delivery logging anywhere in
 * this repo today; that gap is shown explicitly via `note`, not hidden. */
function ExternalIntegrationsSection(props) {
  if (props.error) {
    return <div className="chart-card" style={{ marginTop: 16 }}><h4>External Integrations</h4><p className="muted">Unavailable — {props.error}</p></div>;
  }
  if (!props.data) {
    return <div className="chart-card" style={{ marginTop: 16 }}><h4>External Integrations</h4><p className="muted">Loading…</p></div>;
  }
  var channels = Object.keys(props.data.by_channel || {});
  return (
    <div className="chart-card" style={{ marginTop: 16 }}>
      <h4>External Integrations (WhatsApp / Email)</h4>
      <p className="muted" style={{ fontSize: "0.8rem" }}>{props.data.note}</p>
      {channels.length === 0 && <p className="muted">No delivery records found.</p>}
      {channels.length > 0 && (
        <table className="table">
          <thead><tr><th>Channel</th><th>Submitted</th><th>Provider accepted</th><th>Failed</th></tr></thead>
          <tbody>
            {channels.map(function (ch) {
              var stats = props.data.by_channel[ch];
              return <tr key={ch}><td>{ch}</td><td>{stats.submitted}</td><td>{stats.provider_accepted}</td><td>{stats.failed}</td></tr>;
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
