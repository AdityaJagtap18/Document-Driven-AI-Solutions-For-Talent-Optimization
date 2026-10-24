/**
 * Admin Panel: Azure Integration Readiness (GET/POST /admin/azure-readiness*).
 *
 * One composed status per Azure integration -- see azure_readiness.py's
 * module docstring for the full 9-state vocabulary (NOT_CONFIGURED,
 * AUTHENTICATION_REQUIRED, READY, etc.) and exactly what evidence each
 * state requires. This page never runs a check on load -- GET only
 * reads already-cached results -- and "Validate" never creates an Azure
 * resource; only the Ephemeral Tests tab's provision/execute action can
 * do that, and only after explicit approval.
 */
var READINESS_BADGE_CLASS = {
  READY: "badge-good", PARTIALLY_VERIFIED: "badge-neutral", DEGRADED: "badge-neutral",
  NOT_CONFIGURED: "badge-bad", AUTHENTICATION_REQUIRED: "badge-bad", PERMISSION_DENIED: "badge-bad",
  CONNECTION_FAILED: "badge-bad", NOT_INTEGRATED: "badge-neutral", UNKNOWN: "badge-neutral",
};

function AzureReadiness(props) {
  var api = props.api;
  var permissions = props.permissions || [];
  var canValidate = permissions.indexOf("azure.manage") !== -1;
  var tabState = React.useState("services");
  var tab = tabState[0];
  var setTab = tabState[1];

  return (
    <div className="view" data-testid="azure-readiness-page">
      <h2>Azure Readiness</h2>
      <p className="muted">
        Centralized Azure integration readiness: one composed status per service (configuration, authentication,
        permissions, and connectivity), real Azure CLI login detection, and the ephemeral provision-test-destroy
        workflow for integrations a read-only check can't fully verify.
      </p>
      <div className="toolbar">
        <button className={"btn" + (tab === "services" ? " btn-primary" : "")} onClick={function () { setTab("services"); }}>Services</button>
        <button className={"btn" + (tab === "ai_observability" ? " btn-primary" : "")} onClick={function () { setTab("ai_observability"); }}>Azure AI &amp; Observability</button>
        <button className={"btn" + (tab === "ephemeral" ? " btn-primary" : "")} onClick={function () { setTab("ephemeral"); }}>Ephemeral Tests</button>
        <button className={"btn" + (tab === "cost" ? " btn-primary" : "")} onClick={function () { setTab("cost"); }}>Cost &amp; Usage</button>
      </div>
      {tab === "services" && <AzureReadinessServicesTab api={api} canValidate={canValidate} />}
      {tab === "ai_observability" && <AzureAiAndObservabilityTab api={api} permissions={permissions} />}
      {tab === "ephemeral" && <AzureReadinessEphemeralTab api={api} canValidate={canValidate} />}
      {tab === "cost" && <AzureCostUsageTab api={api} />}
    </div>
  );
}

function AzureReadinessServicesTab(props) {
  var api = props.api;
  var state = React.useState({ loading: true, error: null, data: null });
  var data = state[0];
  var setData = state[1];
  var cliState = React.useState(null);
  var cliStatus = cliState[0];
  var setCliStatus = cliState[1];
  var validatingState = React.useState(null);
  var validatingId = validatingState[0];
  var setValidatingId = validatingState[1];

  function load(bypassCache) {
    if (bypassCache) {
      window.CDPI_AZURE_CACHE.invalidate("azure-readiness:list");
      window.CDPI_AZURE_CACHE.invalidate("azure-readiness:cli-status");
    }
    setData(function (d) { return Object.assign({}, d, { loading: true, error: null }); });
    window.CDPI_AZURE_CACHE.get("azure-readiness:list", function () { return api.getAzureReadiness(); })
      .then(function (result) { setData({ loading: false, error: null, data: result }); })
      .catch(function (err) { setData({ loading: false, error: err.message, data: null }); });
    window.CDPI_AZURE_CACHE.get("azure-readiness:cli-status", function () { return api.getAzureReadinessCliStatus(); })
      .then(setCliStatus).catch(function () { setCliStatus(null); });
  }

  React.useEffect(function () { load(false); }, []);

  function validateOne(integrationId) {
    setValidatingId(integrationId);
    api.validateAzureReadiness(integrationId).then(function () { load(true); }).catch(function (err) { window.alert(err.message); }).then(function () { setValidatingId(null); });
  }

  function validateAll() {
    setValidatingId("__all__");
    api.validateAllAzureReadiness().then(function () { load(true); }).catch(function (err) { window.alert(err.message); }).then(function () { setValidatingId(null); });
  }

  if (data.loading) return <p className="muted">Loading…</p>;
  if (data.error) return <div className="banner banner-error">Couldn't load Azure readiness: {data.error}</div>;

  return (
    <div data-testid="readiness-check-result">
      {cliStatus && (
        <div className="chart-card" style={{ marginBottom: 16 }}>
          <h4>Azure CLI</h4>
          <p>
            <span className={"badge " + (cliStatus.account.authenticated ? "badge-good" : "badge-bad")}>
              {cliStatus.account.authenticated ? "Authenticated" : "Not authenticated"}
            </span>
          </p>
          <p className="muted" style={{ fontSize: "0.85rem" }}>{cliStatus.account.detail}</p>
          {cliStatus.account.authenticated && (
            <p className="muted" style={{ fontSize: "0.85rem" }}>
              Subscription: {cliStatus.account.subscription_name} ({cliStatus.account.subscription_id})
            </p>
          )}
        </div>
      )}

      {props.canValidate && (
        <div className="toolbar">
          <button className="btn" data-testid="run-readiness-check" onClick={validateAll} disabled={validatingId === "__all__"}>
            {validatingId === "__all__" ? "Validating all…" : "Validate all"}
          </button>
        </div>
      )}

      <div className="table-card">
        <table className="candidate-table">
          <thead>
            <tr><th>Service</th><th>Status</th><th>Reason</th><th>Last validated</th><th>Next action</th><th></th></tr>
          </thead>
          <tbody>
            {data.data.services.map(function (svc) {
              return (
                <tr key={svc.adapter_type}>
                  <td>{svc.service_name}</td>
                  <td data-testid="azure-readiness-status">
                    <span className={"badge " + (READINESS_BADGE_CLASS[svc.status] || "badge-neutral")}>{svc.status}</span>
                  </td>
                  <td className="muted" style={{ fontSize: "0.82rem" }}>{svc.reason}</td>
                  <td className="muted" style={{ fontSize: "0.82rem" }}>{svc.last_validated_at || "Never"}</td>
                  <td className="muted" style={{ fontSize: "0.82rem" }}>{svc.recommended_next_action}</td>
                  <td>
                    {props.canValidate && svc.integration_id && (
                      <button className="btn" onClick={function () { validateOne(svc.integration_id); }} disabled={validatingId === svc.integration_id}>
                        {validatingId === svc.integration_id ? "Validating…" : "Validate"}
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function AzureAiAndObservabilityTab(props) {
    var api = props.api;
    var permissions = props.permissions || [];
    var canTestAi = permissions.indexOf("azure.manage") !== -1;
    var canTestTelemetry = permissions.indexOf("observability.read") !== -1;
    var aiState = React.useState(null);
    var aiResult = aiState[0];
    var setAiResult = aiState[1];
    var aiBusyState = React.useState(false);
    var aiBusy = aiBusyState[0];
    var setAiBusy = aiBusyState[1];
    var telState = React.useState(null);
    var telResult = telState[0];
    var setTelResult = telState[1];
    var telBusyState = React.useState(false);
    var telBusy = telBusyState[0];
    var setTelBusy = telBusyState[1];

    function runAiTest() {
        setAiBusy(true);
        api.testAzureAiFoundry().then(setAiResult).catch(function (err) { window.alert(err.message); }).then(function () { setAiBusy(false); });
    }
    function runTelemetryTest() {
        setTelBusy(true);
        api.testApplicationInsightsTelemetry().then(setTelResult).catch(function (err) { window.alert(err.message); }).then(function () { setTelBusy(false); });
    }

    return (
        <div>
            <div className="chart-card" style={{ marginBottom: 16 }}>
                <h4>Azure OpenAI / AI Foundry</h4>
                <p className="muted">
                    Sends ONE real, minimal chat completion and embedding request to the configured Azure OpenAI
                    endpoint -- never run automatically (this is a real, billable Azure call). Reports an honest
                    "not configured" result if AZURE_OPENAI_ENDPOINT/AZURE_OPENAI_API_KEY/AZURE_OPENAI_CHAT_DEPLOYMENT
                    aren't all set.
                </p>
                {canTestAi && (
                    <button className="btn" onClick={runAiTest} disabled={aiBusy}>{aiBusy ? "Running test request…" : "Run real test request"}</button>
                )}
                {aiResult && (
                    <div style={{ marginTop: 10 }}>
                        {!aiResult.configured && <p className="muted">Not configured.</p>}
                        {aiResult.configured && (
                            <ul>
                                <li>Chat: <span className={"badge " + (aiResult.chat && aiResult.chat.outcome === "succeeded" ? "badge-good" : "badge-bad")}>{aiResult.chat ? aiResult.chat.outcome : "n/a"}</span></li>
                                <li>Embedding: <span className={"badge " + (aiResult.embedding && aiResult.embedding.outcome === "succeeded" ? "badge-good" : "badge-neutral")}>{aiResult.embedding ? aiResult.embedding.outcome : "n/a"}</span></li>
                            </ul>
                        )}
                    </div>
                )}
            </div>

            <div className="chart-card">
                <h4>Application Insights Telemetry</h4>
                <p className="muted">
                    Emits one real span through this app's normal telemetry path with a unique correlation ID.
                    Confirms the span was written locally and (if configured) handed to the Application Insights
                    exporter -- never claims Azure actually finished ingesting it, since that needs checking
                    Application Insights itself a few minutes later.
                </p>
                {canTestTelemetry && (
                    <button className="btn" onClick={runTelemetryTest} disabled={telBusy}>{telBusy ? "Running…" : "Run connectivity test"}</button>
                )}
                {telResult && (
                    <div style={{ marginTop: 10 }}>
                        <p>Correlation ID: <code>{telResult.correlation_id}</code></p>
                        <p>
                            <span className={"badge " + (telResult.application_insights_handed_off ? "badge-good" : "badge-neutral")}>
                                {telResult.application_insights_handed_off ? "Handed to Application Insights" : "Local log only"}
                            </span>
                        </p>
                        <p className="muted" style={{ fontSize: "0.85rem" }}>{telResult.note}</p>
                    </div>
                )}
            </div>
        </div>
    );
}

function AzureCostUsageTab(props) {
    var api = props.api;
    var dataState = React.useState(null);
    var data = dataState[0];
    var setData = dataState[1];
    var busyState = React.useState(false);
    var busy = busyState[0];
    var setBusy = busyState[1];
    var errorState = React.useState(null);
    var error = errorState[0];
    var setError = errorState[1];

    function load(bypassCache) {
        if (bypassCache) window.CDPI_AZURE_CACHE.invalidate("azure-cost-usage");
        setBusy(true);
        setError(null);
        window.CDPI_AZURE_CACHE.get("azure-cost-usage", function () { return api.getAzureCostUsage(); })
            .then(setData)
            .catch(function (err) { setError(err.message); })
            .then(function () { setBusy(false); });
    }

    React.useEffect(function () { load(false); }, []);

    return (
        <div>
            <div className="chart-card" style={{ marginBottom: 16 }}>
                <h4>Real Resources &amp; Month-to-Date Cost</h4>
                <p className="muted">
                    Live `az resource list` + a direct call to the Azure Cost Management Query API, scoped to
                    AZURE_RESOURCE_GROUP -- never a fabricated or cached total. Requires a local `az login` session
                    (see the Services tab's CLI status) and only works in real mode.
                </p>
                <button className="btn" onClick={function () { load(true); }} disabled={busy}>{busy ? "Refreshing…" : "Refresh"}</button>
                {error && <p className="muted" style={{ color: "var(--color-danger, #b91c1c)" }}>{error}</p>}
            </div>

            {data && !data.configured && (
                <p className="muted">{data.resources.detail}</p>
            )}

            {data && data.configured && (
                <React.Fragment>
                    <div className="chart-card" style={{ marginBottom: 16 }}>
                        <h4>Resource group: <code>{data.resource_group}</code></h4>
                        <p className="muted">{data.resources.detail}</p>
                        <table className="table">
                            <thead><tr><th>Name</th><th>Type</th><th>Location</th><th>Status</th></tr></thead>
                            <tbody>
                                {data.resources.resources.map(function (r) {
                                    return (
                                        <tr key={r.name}>
                                            <td>{r.name}</td>
                                            <td>{r.type}</td>
                                            <td>{r.location}</td>
                                            <td><span className={"badge " + (r.status === "Succeeded" ? "badge-good" : "badge-neutral")}>{r.status}</span></td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>

                    <div className="chart-card">
                        <h4>Month-to-date cost</h4>
                        {!data.cost.available && <p className="muted">{data.cost.detail}</p>}
                        {data.cost.available && (
                            <React.Fragment>
                                <p>Total: <strong>{data.cost.total} {data.cost.currency}</strong></p>
                                <p className="muted" style={{ fontSize: "0.85rem" }}>{data.cost.detail}</p>
                                <table className="table">
                                    <thead><tr><th>Resource</th><th>Cost ({data.cost.currency})</th></tr></thead>
                                    <tbody>
                                        {data.cost.by_resource.map(function (r) {
                                            return (
                                                <tr key={r.name}>
                                                    <td>{r.name}</td>
                                                    <td>{r.cost}</td>
                                                </tr>
                                            );
                                        })}
                                    </tbody>
                                </table>
                            </React.Fragment>
                        )}
                    </div>
                </React.Fragment>
            )}
        </div>
    );
}

function AzureReadinessEphemeralTab(props) {
  var api = props.api;
  var state = React.useState({ loading: true, error: null, data: null });
  var data = state[0];
  var setData = state[1];
  var regionState = React.useState("eastus");
  var region = regionState[0];
  var setRegion = regionState[1];
  var busyState = React.useState(null);
  var busyRunId = busyState[0];
  var setBusyRunId = busyState[1];

  function load() {
    api.listEphemeralTests()
      .then(function (result) { setData({ loading: false, error: null, data: result }); })
      .catch(function (err) { setData({ loading: false, error: err.message, data: null }); });
  }

  React.useEffect(function () { load(); }, []);

  function plan() {
    setBusyRunId("__plan__");
    api.planEphemeralTest("blob_storage_test", region).then(load).catch(function (err) { window.alert(err.message); }).then(function () { setBusyRunId(null); });
  }
  function approve(runId) {
    setBusyRunId(runId);
    api.approveEphemeralTest(runId).then(load).catch(function (err) { window.alert(err.message); }).then(function () { setBusyRunId(null); });
  }
  function execute(runId) {
    setBusyRunId(runId);
    api.executeEphemeralTest(runId).then(load).catch(function (err) { window.alert(err.message); }).then(function () { setBusyRunId(null); });
  }
  function cleanup(runId) {
    setBusyRunId(runId);
    api.cleanupEphemeralTest(runId).then(load).catch(function (err) { window.alert(err.message); }).then(function () { setBusyRunId(null); });
  }

  if (data.loading) return <p className="muted">Loading…</p>;
  if (data.error) return <div className="banner banner-error">Couldn't load ephemeral test runs: {data.error}</div>;

  return (
    <div>
      <div className="banner banner-error">
        Mode C: provision -&gt; test -&gt; destroy. "Plan" is pure local computation (no Azure call). "Execute" is the
        ONLY action anywhere in this app that can create real Azure resources, and only after you explicitly approve
        the plan -- it will honestly report a BLOCKED status with the exact reason if Azure CLI authentication or
        the CLI itself isn't available, rather than fabricating a successful deployment.
      </div>

      {props.canValidate && (
        <div className="toolbar">
          <label>Region</label>
          <select value={region} onChange={function (e) { setRegion(e.target.value); }}>
            {["eastus", "eastus2", "westus2", "centralus", "westeurope"].map(function (r) { return <option key={r} value={r}>{r}</option>; })}
          </select>
          <button className="btn btn-primary" onClick={plan} disabled={busyRunId === "__plan__"}>
            {busyRunId === "__plan__" ? "Planning…" : "Plan Blob Storage test"}
          </button>
        </div>
      )}

      <div className="table-card">
        <table className="candidate-table">
          <thead>
            <tr><th>Run</th><th>Status</th><th>Resource group</th><th>Approved</th><th>Detail</th><th>Actions</th></tr>
          </thead>
          <tbody>
            {data.data.runs.length === 0 && <tr><td colSpan={6} className="muted">No ephemeral test runs yet.</td></tr>}
            {data.data.runs.map(function (run) {
              return (
                <tr key={run.run_id}>
                  <td>{run.run_id}</td>
                  <td><span className={"badge " + (run.status && run.status.indexOf("FAILED") !== -1 ? "badge-bad" : run.status === "CLEANUP_VERIFIED" || run.status === "TEST_PASSED" ? "badge-good" : "badge-neutral")}>{run.status}</span></td>
                  <td className="muted" style={{ fontSize: "0.82rem" }}>{run.resource_group_name}</td>
                  <td>{run.approved ? "Yes" : "No"}</td>
                  <td className="muted" style={{ fontSize: "0.82rem" }}>{run.blocked_reason || run.test_detail || "-"}</td>
                  <td style={{ display: "flex", gap: 6 }}>
                    {props.canValidate && run.status === "AWAITING_APPROVAL" && !run.approved && (
                      <button className="btn" onClick={function () { approve(run.run_id); }} disabled={busyRunId === run.run_id}>Approve</button>
                    )}
                    {props.canValidate && run.approved && (run.status === "AWAITING_APPROVAL" || run.status === "PLANNED") && (
                      <button className="btn" onClick={function () { execute(run.run_id); }} disabled={busyRunId === run.run_id}>Execute</button>
                    )}
                    {props.canValidate && ["PROVISIONED", "TESTING", "TEST_PASSED", "TEST_FAILED", "CLEANUP_IN_PROGRESS", "CLEANUP_FAILED"].indexOf(run.status) !== -1 && (
                      <button className="btn" onClick={function () { cleanup(run.run_id); }} disabled={busyRunId === run.run_id}>Cleanup</button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
