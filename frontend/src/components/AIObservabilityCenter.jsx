/**
 * Admin Panel: AI Observability Center (GET /admin/ai-observability/*).
 *
 * Two real data sources, merged server-side (api_blueprint.py's
 * ai_observability_summary_endpoint), not duplicated here:
 *  - logs/llm_observability.jsonl (existing, per-LLM-call token/cost/
 *    latency -- same data Dashboard.jsx's three stat tiles already show)
 *  - logs/ai_traces.jsonl (one entry per AI PIPELINE SPAN -- a trace_id
 *    groups every span of one end-to-end workflow run, e.g. one resume's
 *    full parse: document extraction -> chunking -> embedding ->
 *    retrieval ranking -> two LLM calls -> output validation ->
 *    persistence, all correlated). This is the one real, single-process,
 *    end-to-end AI workflow in this repo (ResumeParser.py) instrumented
 *    start to finish -- see docs/AI_OBSERVABILITY_AZURE_HUB.md's
 *    investigation notes on why that's the highest-value real workflow
 *    to trace here (ResumeMEtrics.py runs as a separate process, so it
 *    can't share one in-process trace with the parsing step).
 *
 * Never fabricates data: an empty log file returns real zeros, and a
 * latency percentile computed from fewer than 5 data points is shown as
 * an explicit "not enough data" state, never a number. In mock mode the
 * trace list is clearly illustrative demo data (see mockApiClient.js's
 * own comment on MOCK_AI_TRACES); the topbar's "API: mock" label is this
 * app's existing, already-established way of marking that.
 *
 * Deliberately NOT built this pass (stated here, not hidden): the AI
 * Evaluation Center (no eval dataset/run infrastructure exists), alert
 * rules (no real notification mechanism exists in this repo to wire
 * them to -- faking "active" alerts was explicitly out of scope), and
 * RAG quality metrics like context precision/faithfulness (no evaluation
 * harness exists to compute them from). See
 * docs/AI_OBSERVABILITY_AZURE_HUB.md for the full list.
 */
function AIObservabilityCenter(props) {
  var api = props.api;
  var tabState = React.useState("overview"); // "overview" | "traces"
  var tab = tabState[0];
  var setTab = tabState[1];
  var selectedTraceState = React.useState(null);
  var selectedTraceId = selectedTraceState[0];
  var setSelectedTraceId = selectedTraceState[1];

  return (
    <div className="view">
      <h2>AI Observability Center</h2>
      <p className="muted">
        What happened during each AI workflow run, stage by stage -- latency, tokens, cost, and failures,
        correlated into real multi-stage traces, not just isolated LLM call metrics.
      </p>

      <div className="toolbar">
        <button className={"btn" + (tab === "overview" ? " btn-primary" : "")} onClick={function () { setTab("overview"); }}>Overview</button>
        <button className={"btn" + (tab === "traces" ? " btn-primary" : "")} onClick={function () { setTab("traces"); setSelectedTraceId(null); }}>Trace Explorer</button>
        <button className={"btn" + (tab === "budget" ? " btn-primary" : "")} onClick={function () { setTab("budget"); }}>Budget &amp; Alerts</button>
        <button className={"btn" + (tab === "cache" ? " btn-primary" : "")} onClick={function () { setTab("cache"); }}>Response Cache</button>
        <button className={"btn" + (tab === "routing" ? " btn-primary" : "")} onClick={function () { setTab("routing"); }}>Model Routing</button>
      </div>

      {tab === "overview" && <AIObservabilityOverview api={api} onOpenTrace={function (id) { setTab("traces"); setSelectedTraceId(id); }} />}
      {tab === "traces" && (
        selectedTraceId
          ? <AITraceDetail api={api} traceId={selectedTraceId} onBack={function () { setSelectedTraceId(null); }} />
          : <AITraceExplorer api={api} onOpenTrace={setSelectedTraceId} />
      )}
      {tab === "budget" && <TokenomicsBudgetPanel api={api} />}
      {tab === "cache" && <LlmCachePanel api={api} />}
      {tab === "routing" && <ModelRoutingPanel api={api} />}
    </div>
  );
}

function AIObservabilityOverview(props) {
  var api = props.api;
  var onOpenTrace = props.onOpenTrace;
  var snapshot = CDPI_REPORT_SNAPSHOT.useReportSnapshot("ai_observability_overview", function () {
    return api.aiObservabilitySummary();
  });
  var data = { loading: snapshot.state.loading && !snapshot.state.data, error: snapshot.state.error, data: snapshot.state.data };
  var load = snapshot.refresh;

  if (data.error && !data.data) return <div className="banner banner-error">Couldn't load observability summary: {data.error}</div>;
  if (data.loading) return <p className="muted">Loading…</p>;
  var d = data.data;

  return (
    <React.Fragment>
      <div className="toolbar">
        <button className="btn" onClick={load} disabled={snapshot.state.loading}>{snapshot.state.loading ? "Refreshing…" : "Refresh"}</button>
        <span className="muted">Snapshot from {snapshot.state.lastUpdated ? new Date(snapshot.state.lastUpdated).toLocaleString() : "just now"}</span>
      </div>

      <h3 className="subsection-heading">AI requests (end-to-end traces)</h3>
      <div className="stat-row">
        <StatTile label="Total Requests" value={d.requests.total} tone="neutral" />
        <StatTile label="Succeeded" value={d.requests.succeeded} tone="good" />
        <StatTile label="Failed" value={d.requests.failed} tone="bad" />
        <StatTile label="Failure Rate" value={d.requests.failure_rate !== null ? (d.requests.failure_rate * 100).toFixed(1) + "%" : "—"} tone="neutral" />
      </div>

      <h3 className="subsection-heading">Trace latency (end-to-end, per request)</h3>
      <LatencyRow stats={d.trace_latency} />

      <h3 className="subsection-heading">Span latency (per pipeline stage)</h3>
      <LatencyRow stats={d.span_latency} />

      <h3 className="subsection-heading">Cost</h3>
      <p className="muted">
        Pricing version {d.cost.pricing_version} (last updated {d.cost.pricing_last_updated}) — {d.cost.pricing_source}
      </p>
      <div className="stat-row">
        <StatTile label="Total Cost" value={"$" + d.cost.total_cost_usd.toFixed(4)} tone="neutral" />
        <StatTile label="Priced Spans" value={d.cost.priced_span_count} tone="neutral" />
      </div>
      <div className="table-card">
        <table className="candidate-table">
          <thead><tr><th>Workflow</th><th>Cost</th></tr></thead>
          <tbody>
            {Object.keys(d.cost.by_workflow).length === 0 && <tr><td colSpan={2} className="muted">No priced spans yet.</td></tr>}
            {Object.keys(d.cost.by_workflow).sort().map(function (wf) {
              return <tr key={wf}><td>{wf}</td><td>${d.cost.by_workflow[wf].toFixed(6)}</td></tr>;
            })}
          </tbody>
        </table>
      </div>

      <h3 className="subsection-heading">Instrumentation coverage</h3>
      <p className="muted">
        {d.instrumentation_coverage.covered_stage_count} of {d.instrumentation_coverage.documented_stage_count} documented
        pipeline stages have at least one recorded span ({d.instrumentation_coverage.coverage_pct}%).
      </p>
      {d.instrumentation_coverage.uninstrumented_stages.length > 0 && (
        <p className="muted">Not yet observed: {d.instrumentation_coverage.uninstrumented_stages.join(", ")}</p>
      )}
      <p className="muted">Not applicable to this repo (no such feature exists): {d.instrumentation_coverage.not_applicable_to_this_repo.join("; ")}</p>

      <h3 className="subsection-heading">By LLM prompt label</h3>
      <div className="table-card">
        <table className="candidate-table">
          <thead><tr><th>Label</th><th>Calls</th><th>Tokens</th><th>Cost</th></tr></thead>
          <tbody>
            {Object.keys(d.llm_calls.by_label).sort().map(function (label) {
              var stats = d.llm_calls.by_label[label];
              return (
                <tr key={label}>
                  <td>{label}</td><td>{stats.calls}</td><td>{stats.tokens.toLocaleString()}</td>
                  <td>${stats.cost_usd.toFixed(4)}</td>
                </tr>
              );
            })}
            {Object.keys(d.llm_calls.by_label).length === 0 && (
              <tr><td colSpan={4} className="muted">No LLM calls recorded yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <h3 className="subsection-heading">Recent traces</h3>
      <div className="table-card">
        <table className="candidate-table">
          <thead><tr><th>Trace</th><th>Workflow</th><th>Status</th><th>Spans</th><th>Duration</th><th>Started</th><th></th></tr></thead>
          <tbody>
            {d.recent_traces.map(function (t) {
              return (
                <tr key={t.trace_id}>
                  <td className="muted">{t.correlation_id || t.trace_id}</td>
                  <td>{t.workflow}</td>
                  <td><span className={"badge " + (t.status === "succeeded" ? "badge-good" : t.status === "failed" ? "badge-bad" : "badge-neutral")}>{t.status}</span></td>
                  <td>{t.span_count}</td>
                  <td className="muted">{t.total_duration_ms !== null ? t.total_duration_ms + " ms" : "—"}</td>
                  <td className="muted">{t.started_at ? new Date(t.started_at).toLocaleString() : "—"}</td>
                  <td><button className="btn" onClick={function () { onOpenTrace(t.trace_id); }}>View</button></td>
                </tr>
              );
            })}
            {d.recent_traces.length === 0 && <tr><td colSpan={7} className="muted">No traces recorded yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </React.Fragment>
  );
}

function LatencyRow(props) {
  var stats = props.stats;
  if (stats.insufficient_sample) {
    return (
      <p className="muted">
        Only {stats.sample_count} data point(s) recorded -- not enough to compute P50/P95/P99 (needs at least 5).
        {stats.avg_ms !== null && <React.Fragment> Average so far: {stats.avg_ms} ms.</React.Fragment>}
      </p>
    );
  }
  return (
    <div className="stat-row">
      <StatTile label="Average" value={stats.avg_ms + " ms"} tone="neutral" />
      <StatTile label="P50" value={stats.p50_ms + " ms"} tone="neutral" />
      <StatTile label="P95" value={stats.p95_ms + " ms"} tone="neutral" />
      <StatTile label="P99" value={stats.p99_ms + " ms"} tone="neutral" />
    </div>
  );
}

function AITraceExplorer(props) {
  var api = props.api;
  var onOpenTrace = props.onOpenTrace;
  var filterState = React.useState({ workflow: "", status: "", correlation_id: "", trace_id: "" });
  var filters = filterState[0];
  var setFilters = filterState[1];
  var pageState = React.useState(0);
  var page = pageState[0];
  var setPage = pageState[1];
  var PAGE_SIZE = 20;

  // Snapshot-cached per filter+page combination -- the first time a given
  // combination is viewed it fetches for real (so changing a filter still
  // gets fresh results, same as before); revisiting that exact
  // filter+page later shows its own cached snapshot instantly.
  var snapshotKey = "ai_trace_explorer:" + JSON.stringify([filters.workflow, filters.status, filters.correlation_id, filters.trace_id, page]);
  var snapshot = CDPI_REPORT_SNAPSHOT.useReportSnapshot(snapshotKey, function () {
    var query = { limit: PAGE_SIZE, offset: page * PAGE_SIZE };
    if (filters.workflow) query.workflow = filters.workflow;
    if (filters.status) query.status = filters.status;
    if (filters.correlation_id) query.correlation_id = filters.correlation_id;
    if (filters.trace_id) query.trace_id = filters.trace_id;
    return api.listAiTraces(query);
  });
  var load = snapshot.refresh;
  var data = {
    loading: snapshot.state.loading && !snapshot.state.data, error: snapshot.state.error,
    traces: (snapshot.state.data && snapshot.state.data.traces) || [],
    count: (snapshot.state.data && snapshot.state.data.count) || 0,
  };

  function setFilter(name, value) {
    setPage(0);
    setFilters(function (f) { return Object.assign({}, f, { [name]: value }); });
  }

  var totalPages = Math.max(1, Math.ceil(data.count / PAGE_SIZE));

  return (
    <div>
      <div className="toolbar">
        <input type="search" placeholder="Search by candidate/correlation id…" value={filters.correlation_id}
          onChange={function (e) { setFilter("correlation_id", e.target.value); }} />
        <select value={filters.workflow} onChange={function (e) { setFilter("workflow", e.target.value); }}>
          <option value="">All workflows</option>
          <option value="resume_parsing">resume_parsing</option>
          <option value="llm">llm</option>
          <option value="document_verification">document_verification</option>
        </select>
        <select value={filters.status} onChange={function (e) { setFilter("status", e.target.value); }}>
          <option value="">Any status</option>
          <option value="succeeded">Succeeded</option>
          <option value="failed">Failed</option>
          <option value="started">Started</option>
        </select>
        <button className="btn" onClick={load}>Refresh</button>
      </div>

      {data.error && <div className="banner banner-error">Couldn't load traces: {data.error}</div>}
      {data.loading && <p className="muted">Loading…</p>}
      {!data.loading && !data.error && (
        <React.Fragment>
          <div className="table-card">
            <table className="candidate-table">
              <thead><tr><th>Trace</th><th>Workflow</th><th>Status</th><th>Spans</th><th>Stages</th><th>Duration</th><th>Started</th><th></th></tr></thead>
              <tbody>
                {data.traces.map(function (t) {
                  return (
                    <tr key={t.trace_id}>
                      <td className="muted">{t.correlation_id || t.trace_id}</td>
                      <td>{t.workflow}</td>
                      <td><span className={"badge " + (t.status === "succeeded" ? "badge-good" : t.status === "failed" ? "badge-bad" : "badge-neutral")}>{t.status}</span></td>
                      <td>{t.span_count}</td>
                      <td className="muted">{t.stages.join(" → ")}</td>
                      <td className="muted">{t.total_duration_ms !== null ? t.total_duration_ms + " ms" : "—"}</td>
                      <td className="muted">{t.started_at ? new Date(t.started_at).toLocaleString() : "—"}</td>
                      <td><button className="btn" onClick={function () { onOpenTrace(t.trace_id); }}>View</button></td>
                    </tr>
                  );
                })}
                {data.traces.length === 0 && <tr><td colSpan={8} className="muted">No traces match these filters.</td></tr>}
              </tbody>
            </table>
          </div>
          <div className="toolbar">
            <button className="btn" onClick={function () { setPage(Math.max(0, page - 1)); }} disabled={page === 0}>Previous</button>
            <span className="muted">Page {page + 1} of {totalPages} ({data.count} total)</span>
            <button className="btn" onClick={function () { setPage(Math.min(totalPages - 1, page + 1)); }} disabled={page + 1 >= totalPages}>Next</button>
          </div>
        </React.Fragment>
      )}
    </div>
  );
}

function AITraceDetail(props) {
  var api = props.api;
  var traceId = props.traceId;
  var onBack = props.onBack;
  var state = React.useState({ loading: true, error: null, trace: null, spans: [] });
  var data = state[0];
  var setData = state[1];

  React.useEffect(function () {
    setData({ loading: true, error: null, trace: null, spans: [] });
    api.getAiTrace(traceId)
      .then(function (result) { setData({ loading: false, error: null, trace: result.trace, spans: result.spans }); })
      .catch(function (err) { setData({ loading: false, error: err.message, trace: null, spans: [] }); });
  }, [traceId]);

  return (
    <div>
      <div className="toolbar"><button className="btn" onClick={onBack}>← Back to Trace Explorer</button></div>

      {data.error && <div className="banner banner-error">Couldn't load trace: {data.error}</div>}
      {data.loading && <p className="muted">Loading…</p>}

      {!data.loading && !data.error && data.trace && (
        <React.Fragment>
          <h3 className="subsection-heading">{data.trace.correlation_id || data.trace.trace_id}</h3>
          <p className="muted">
            Trace ID <code>{data.trace.trace_id}</code> — workflow {data.trace.workflow} —{" "}
            <span className={"badge " + (data.trace.status === "succeeded" ? "badge-good" : data.trace.status === "failed" ? "badge-bad" : "badge-neutral")}>{data.trace.status}</span>
            {" "}— {data.trace.span_count} span(s), {data.trace.total_duration_ms !== null ? data.trace.total_duration_ms + " ms total" : "duration unavailable"}
            {data.trace.total_cost_usd !== null && <React.Fragment>, ${data.trace.total_cost_usd.toFixed(6)} estimated cost</React.Fragment>}
          </p>

          <div className="table-card">
            <table className="candidate-table">
              <thead><tr><th>#</th><th>Stage</th><th>Status</th><th>Duration</th><th>Model</th><th>Tokens</th><th>Cost</th><th>Error</th></tr></thead>
              <tbody>
                {data.spans.map(function (s, i) {
                  var meta = s.metadata || {};
                  return (
                    <tr key={s.span_id || i}>
                      <td className="muted">{i + 1}</td>
                      <td>{s.stage}</td>
                      <td><span className={"badge " + (s.status === "succeeded" ? "badge-good" : s.status === "failed" ? "badge-bad" : "badge-neutral")}>{s.status}</span></td>
                      <td className="muted">{s.duration_ms !== null && s.duration_ms !== undefined ? s.duration_ms + " ms" : "—"}</td>
                      <td className="muted">{meta.model || "—"}{meta.prompt_version ? " (" + meta.prompt_version + ")" : ""}</td>
                      <td className="muted">{meta.total_tokens !== undefined ? meta.total_tokens : "—"}</td>
                      <td className="muted">{meta.cost_usd !== undefined && meta.cost_usd !== null ? "$" + meta.cost_usd.toFixed(6) : "—"}</td>
                      <td className="banner-error-inline">{s.error || ""}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </React.Fragment>
      )}
    </div>
  );
}

/**
 * F-02 (Tokenomics calculator, minimal slice): real, period-scoped
 * (current UTC calendar month) usage + a real, versioned, admin-
 * configurable budget/warning-threshold check -- see tokenomics.py's
 * module docstring and docs/fde/IMPLEMENTATION_STATUS.md for exactly
 * what's in and out of scope for this slice. This is deliberately added
 * as a tab on the EXISTING AI Observability Center (which already shows
 * all-time cost/token data) rather than a new, disconnected page.
 */
function TokenomicsBudgetPanel(props) {
  var api = props.api;
  var state = React.useState({ loading: true, error: null, active: null, draft: null, status: null });
  var data = state[0];
  var setData = state[1];
  var formState = React.useState({ monthly_budget_usd: "", warning_threshold_pct: "80" });
  var form = formState[0];
  var setForm = formState[1];
  var saveState = React.useState({ saving: false, error: null });
  var save = saveState[0];
  var setSave = saveState[1];

  function load() {
    setData(function (d) { return Object.assign({}, d, { loading: true, error: null }); });
    Promise.all([api.getTokenomicsConfig(), api.getTokenomicsStatus()])
      .then(function (results) {
        var cfg = results[0];
        var status = results[1];
        var effective = cfg.draft || cfg.active;
        setForm({
          monthly_budget_usd: effective.monthly_budget_usd === null || effective.monthly_budget_usd === undefined ? "" : String(effective.monthly_budget_usd),
          warning_threshold_pct: String(effective.warning_threshold_pct != null ? effective.warning_threshold_pct : 80),
        });
        setData({ loading: false, error: null, active: cfg.active, draft: cfg.draft, status: status });
      })
      .catch(function (err) { setData(function (d) { return Object.assign({}, d, { loading: false, error: err.message }); }); });
  }

  React.useEffect(function () { load(); }, []);

  function saveDraft() {
    setSave({ saving: true, error: null });
    var budget = form.monthly_budget_usd.trim() === "" ? null : Number(form.monthly_budget_usd);
    var threshold = parseInt(form.warning_threshold_pct, 10);
    api.saveTokenomicsConfigDraft({ monthly_budget_usd: budget, warning_threshold_pct: threshold })
      .then(function () { setSave({ saving: false, error: null }); load(); })
      .catch(function (err) { setSave({ saving: false, error: err.message }); });
  }

  function activate() {
    setSave({ saving: true, error: null });
    api.activateTokenomicsConfig()
      .then(function () { setSave({ saving: false, error: null }); load(); })
      .catch(function (err) { setSave({ saving: false, error: err.message }); });
  }

  if (data.error) return <div className="banner banner-error">Couldn't load budget configuration: {data.error}</div>;
  if (data.loading) return <p className="muted">Loading…</p>;

  var status = data.status.budget;
  var usage = data.status.usage;
  var statusTone = status.status === "exceeded" ? "bad" : status.status === "warning" ? "warn" : status.status === "ok" ? "good" : "neutral";

  return (
    <React.Fragment>
      <div className="toolbar"><button className="btn" onClick={load}>Refresh</button></div>

      <h3 className="subsection-heading">This month's usage ({usage.period})</h3>
      <div className="stat-row">
        <StatTile label="Spent" value={"$" + usage.total_cost_usd.toFixed(4)} tone="neutral" />
        <StatTile label="Calls" value={usage.call_count} tone="neutral" />
        <StatTile label="Tokens" value={usage.total_tokens.toLocaleString()} tone="neutral" />
        <StatTile label="Budget Status" value={status.status.replace("_", " ")} tone={statusTone} />
      </div>
      {usage.unknown_cost_call_count > 0 && (
        <p className="muted">
          {usage.unknown_cost_call_count} call(s) this month have no recorded cost -- excluded from the total above, not counted as $0.
        </p>
      )}
      {status.configured && (
        <p className="muted">
          ${status.spent_usd.toFixed(4)} of ${status.budget_usd} budget ({status.percent_used}% used).
        </p>
      )}
      {!status.configured && <p className="muted">No monthly budget configured -- set one below to enable warning/exceeded alerts.</p>}

      <h3 className="subsection-heading">Budget configuration</h3>
      {data.draft && <p className="muted">An unsaved draft exists below. Activate it to make it the real budget check.</p>}
      <label className="login-field">
        Monthly budget (USD, blank = disabled)
        <input type="number" min="0" step="0.01" value={form.monthly_budget_usd}
          onChange={function (e) { setForm(Object.assign({}, form, { monthly_budget_usd: e.target.value })); }} />
      </label>
      <label className="login-field">
        Warning threshold (% of budget)
        <input type="number" min="1" max="100" step="1" value={form.warning_threshold_pct}
          onChange={function (e) { setForm(Object.assign({}, form, { warning_threshold_pct: e.target.value })); }} />
      </label>
      {save.error && <div className="banner banner-error">{save.error}</div>}
      <div className="toolbar">
        <button className="btn" disabled={save.saving} onClick={saveDraft}>Save as Draft</button>
        <button className="btn btn-primary" disabled={save.saving || !data.draft} onClick={activate}>Activate Draft</button>
      </div>
      <p className="muted">
        Active: {data.active.monthly_budget_usd === null ? "no budget set" : "$" + data.active.monthly_budget_usd + "/mo"}, warning at {data.active.warning_threshold_pct}%.
      </p>
    </React.Fragment>
  );
}

/**
 * F-18 (LLM caching, minimal slice). Exact-match caching only, scoped
 * to ResumeParser.py's two cacheable call sites -- see llm_cache.py's
 * module docstring for exactly why only those two, and why this is
 * safe against cross-candidate leakage by construction (the cache key
 * already includes each candidate's own resume content).
 */
function LlmCachePanel(props) {
  var api = props.api;
  var state = React.useState({ loading: true, error: null, data: null });
  var data = state[0];
  var setData = state[1];
  var clearState = React.useState({ clearing: false, error: null });
  var clearing = clearState[0];
  var setClearing = clearState[1];

  function load() {
    setData(function (d) { return Object.assign({}, d, { loading: true, error: null }); });
    api.getLlmCacheStats()
      .then(function (result) { setData({ loading: false, error: null, data: result }); })
      .catch(function (err) { setData(function (d) { return Object.assign({}, d, { loading: false, error: err.message }); }); });
  }

  React.useEffect(function () { load(); }, []);

  function clearCache() {
    setClearing({ clearing: true, error: null });
    api.clearLlmCache()
      .then(function () { setClearing({ clearing: false, error: null }); load(); })
      .catch(function (err) { setClearing({ clearing: false, error: err.message }); });
  }

  if (data.error) return <div className="banner banner-error">Couldn't load cache stats: {data.error}</div>;
  if (data.loading) return <p className="muted">Loading…</p>;
  var d = data.data;

  return (
    <React.Fragment>
      <div className="toolbar">
        <button className="btn" onClick={load}>Refresh</button>
        <button className="btn" disabled={clearing.clearing} onClick={clearCache}>Clear Cache</button>
      </div>
      {clearing.error && <div className="banner banner-error">{clearing.error}</div>}
      <p className="muted">
        Exact-match caching for resume-processing LLM calls only (chunk summarization and structured extraction) --
        the one real case where identical input (reprocessing the same resume/JD) genuinely recurs and the result
        is only ever written back into that same candidate's own record. Chatbots, interview-question generation,
        and evaluation judges are not cached.
      </p>
      <div className="stat-row">
        <StatTile label="Cached Entries" value={d.cache.entry_count} tone="neutral" />
        <StatTile label="Total Hits (All Time)" value={d.cache.total_hit_count} tone="good" />
        <StatTile label="Avoided Calls This Month" value={d.cache_hit_call_count_this_month} tone="good" />
        <StatTile label="Estimated Savings This Month" value={d.estimated_savings_usd_this_month !== null ? "$" + d.estimated_savings_usd_this_month.toFixed(4) : "Not enough data"} tone="neutral" />
      </div>
      <div className="table-card">
        <table className="candidate-table">
          <thead><tr><th>Label</th><th>Cached Entries</th><th>Hits</th></tr></thead>
          <tbody>
            {Object.keys(d.cache.by_label).map(function (label) {
              var s = d.cache.by_label[label];
              return <tr key={label}><td>{label}</td><td>{s.entry_count}</td><td>{s.hit_count}</td></tr>;
            })}
            {Object.keys(d.cache.by_label).length === 0 && <tr><td colSpan={3} className="muted">Cache is empty.</td></tr>}
          </tbody>
        </table>
      </div>
    </React.Fragment>
  );
}

var MODEL_ROUTING_LABEL_TEXT = {
  chat_candidate_qa: "Candidate Chat Q&A", interview_questions_generation: "Interview Question Generation",
  interview_bot_answer_scoring: "Interview Answer Scoring (voicebot CLI -- not live-wired, see note below)",
  rag_groundedness_judge: "RAG Groundedness Judge", ai_match_quality_judge: "AI Match-Quality Judge",
  jd_generation: "JD Generation (AI draft)", jd_document_extraction: "JD Document Extraction (upload)",
};

/**
 * F-17 (model routing, minimal slice). A centralized override for the 6
 * real LLM call sites that had NO admin-configurable model before this
 * feature -- resume-processing's 2 steps already have their own model
 * field in the RAG Configuration Console and are deliberately not
 * duplicated here. See model_routing.py's module docstring.
 */
function ModelRoutingPanel(props) {
  var api = props.api;
  var state = React.useState({ loading: true, error: null, data: null });
  var data = state[0];
  var setData = state[1];
  var formState = React.useState({});
  var form = formState[0];
  var setForm = formState[1];
  var saveState = React.useState({ saving: false, error: null, savedAt: null });
  var save = saveState[0];
  var setSave = saveState[1];

  function load() {
    setData(function (d) { return Object.assign({}, d, { loading: true, error: null }); });
    api.getModelRoutingConfig()
      .then(function (result) {
        setForm(Object.assign({}, result.routes));
        setData({ loading: false, error: null, data: result });
      })
      .catch(function (err) { setData(function (d) { return Object.assign({}, d, { loading: false, error: err.message }); }); });
  }

  React.useEffect(function () { load(); }, []);

  function saveRoutes() {
    setSave({ saving: true, error: null, savedAt: null });
    var routes = {};
    Object.keys(form).forEach(function (label) { if (form[label] && form[label].trim()) routes[label] = form[label].trim(); });
    api.saveModelRoutingConfig(routes)
      .then(function () { setSave({ saving: false, error: null, savedAt: new Date().toISOString() }); load(); })
      .catch(function (err) { setSave({ saving: false, error: err.message, savedAt: null }); });
  }

  if (data.error) return <div className="banner banner-error">Couldn't load model routing config: {data.error}</div>;
  if (data.loading) return <p className="muted">Loading…</p>;
  var d = data.data;

  return (
    <React.Fragment>
      <div className="toolbar"><button className="btn" onClick={load}>Refresh</button></div>
      <p className="muted">
        Sets which model each of these real LLM call sites uses. Leave a field blank to keep using that
        site's real default ({"gpt-3.5-turbo"} today for every one of these -- unchanged unless overridden here).
        Resume processing's chunk-summarization and structured-extraction steps are configured separately,
        in the RAG Configuration Console.
      </p>
      {save.error && <div className="banner banner-error">{save.error}</div>}
      {save.savedAt && <div className="banner" style={{ borderColor: "var(--good)" }}>Saved.</div>}
      <div className="table-card">
        <table className="candidate-table">
          <thead><tr><th>Call Site</th><th>Model Override</th><th>Default</th></tr></thead>
          <tbody>
            {d.routable_labels.map(function (label) {
              return (
                <tr key={label}>
                  <td>{MODEL_ROUTING_LABEL_TEXT[label] || label}</td>
                  <td>
                    <input type="text" placeholder={d.default_models[label]} value={form[label] || ""}
                      onChange={function (e) { setForm(Object.assign({}, form, { [label]: e.target.value })); }} />
                  </td>
                  <td className="muted">{d.default_models[label]}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="toolbar">
        <button className="btn btn-primary" disabled={save.saving} onClick={saveRoutes}>Save Routing</button>
      </div>
      {d.updated_by && <p className="muted">Last updated by {d.updated_by} at {d.updated_at ? new Date(d.updated_at).toLocaleString() : "—"}.</p>}
    </React.Fragment>
  );
}

// StatTile is defined globally by Dashboard.jsx -- reused here (no
// bundler/module system in this app, see frontend/README.md), same
// pattern RecruiterReports.jsx already uses to reuse AdminReports.jsx's
// ChartCanvas/CHART_COLORS/countBy.
