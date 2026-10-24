/**
 * Admin Panel: Azure Service Hub v2 (GET/POST /admin/azure-services*).
 *
 * An ADAPTER REGISTRY (what this repo has real code for -- see
 * azure_integrations.py's ADAPTER_TYPES, verified against the repository,
 * not invented) plus administrator-REGISTERED INSTANCES of those adapters
 * (repositories.py's integration_instances/integration_health_checks).
 *
 * Deliberately does NOT collapse status into one green dot: each instance
 * shows configured/enabled/last-check-outcome as separate badges, because
 * they're genuinely different facts (an integration can be configured but
 * disabled, or enabled but its last check was unhealthy). "Remove
 * registration" (this page only), "disable" (CDPI stops using it), and
 * "the real Azure resource" are three distinct, never-conflated actions --
 * this page has no code path that can delete an actual Azure resource.
 *
 * Resource discovery is real Azure Resource Manager (azure-mgmt-resource),
 * only for integrations whose adapter declares a known azure_resource_type
 * (cosmos_db, blob_storage) and only once subscription_id+resource_group
 * are set in that instance's config -- every other case reports a clear
 * "not supported"/"scope required" message, never a bare empty list that
 * could be misread as "zero resources exist". No create/update/delete
 * resource operation is implemented for any adapter in this pass -- stated
 * here, not hidden.
 */
function AzureServiceHub(props) {
  var api = props.api;
  var permissions = props.permissions || [];
  var viewState = React.useState({ screen: "list", integrationId: null });
  var view = viewState[0];
  var setView = viewState[1];

  function openDetail(id) { setView({ screen: "detail", integrationId: id }); }
  function backToList() { setView({ screen: "list", integrationId: null }); }

  return (
    <div className="view">
      <h2>Azure Service Hub</h2>
      <p className="muted">
        The registry of Azure (and Key Vault / Application Insights) integrations this repo has real,
        verified code for, each as a registered instance with its own configuration, health history, and
        (where genuinely supported) real Azure resource discovery. Statuses are shown as distinct facts, not
        collapsed into one green indicator.
      </p>
      {window.CDPI_CONFIG.mode === "mock" && (
        <div className="banner banner-error">
          Mock mode: no real secrets, network access, or Azure Resource Manager credentials exist in this
          browser-only demo -- every check reports "unknown" and every resource discovery reports "not
          supported", honestly, same as a real unprovisioned deployment would. Switch config.js to mode:"real"
          against a deployed backend to see actual status.
        </div>
      )}

      {view.screen === "list" && <AzureIntegrationsList api={api} permissions={permissions} onOpenDetail={openDetail} />}
      {view.screen === "detail" && <AzureIntegrationDetail api={api} permissions={permissions} integrationId={view.integrationId} onBack={backToList} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Status vocabulary helpers -- shared between the list and detail views so
// the same outcome always renders the same badge.
// ---------------------------------------------------------------------------
function outcomeBadgeClass(outcome) {
  if (outcome === "healthy") return "badge-good";
  if (outcome === "unhealthy") return "badge-bad";
  if (outcome === "degraded") return "badge-neutral";
  return "badge-neutral"; // unknown / null
}

function outcomeLabel(outcome) {
  return outcome || "never checked";
}

// ---------------------------------------------------------------------------
// Inventory list
// ---------------------------------------------------------------------------
function AzureIntegrationsList(props) {
  var api = props.api;
  var permissions = props.permissions;
  var onOpenDetail = props.onOpenDetail;
  var canRegister = permissions.indexOf("azure.register") !== -1;

  var state = React.useState({ loading: true, error: null, integrations: [], plannedAdapters: [] });
  var data = state[0];
  var setData = state[1];
  var showAddState = React.useState(false);
  var showAdd = showAddState[0];
  var setShowAdd = showAddState[1];

  function load(bypassCache) {
    if (bypassCache) window.CDPI_AZURE_CACHE.invalidatePrefix("azure-service-hub:");
    setData(function (d) { return Object.assign({}, d, { loading: true, error: null }); });
    Promise.all([
      window.CDPI_AZURE_CACHE.get("azure-service-hub:integrations", function () { return api.listAzureIntegrations(); }),
      window.CDPI_AZURE_CACHE.get("azure-service-hub:adapters", function () { return api.listAzureAdapters(); }),
    ])
      .then(function (results) {
        var registeredAdapterTypes = results[0].integrations.map(function (i) { return i.adapter_type; });
        var planned = results[1].adapters.filter(function (a) { return a.status === "planned" && registeredAdapterTypes.indexOf(a.adapter_type) === -1; });
        setData({ loading: false, error: null, integrations: results[0].integrations, plannedAdapters: planned });
      })
      .catch(function (err) { setData(function (d) { return Object.assign({}, d, { loading: false, error: err.message }); }); });
  }

  React.useEffect(function () { load(false); }, []);

  return (
    <React.Fragment>
      <div className="toolbar">
        <button className="btn" onClick={function () { load(true); }}>Refresh</button>
        {canRegister && <button className="btn btn-primary" onClick={function () { setShowAdd(true); }}>Add Integration</button>}
      </div>

      {data.error && <div className="banner banner-error">Couldn't load integrations: {data.error}</div>}
      {data.loading && <p className="muted">Loading…</p>}

      {!data.loading && !data.error && (
        <div className="integration-grid">
          {data.integrations.map(function (integration) {
            return (
              <div className="integration-card" key={integration.integration_id}
                onClick={function () { onOpenDetail(integration.integration_id); }} style={{ cursor: "pointer" }}>
                <div className="integration-card-header">
                  <div>
                    <div className="integration-card-name">{integration.display_name}</div>
                    <div className="integration-card-category">{integration.category} · {integration.environment}</div>
                  </div>
                </div>
                <div className="integration-card-secrets">
                  <span className={"badge " + (integration.configured ? "badge-good" : "badge-neutral")}>
                    {integration.configured ? "configured" : "not configured"}
                  </span>
                  <span className={"badge " + (integration.enabled ? "badge-good" : "badge-neutral")}>
                    {integration.enabled ? "enabled" : "disabled"}
                  </span>
                  <span className={"badge " + outcomeBadgeClass(integration.last_connection_outcome)}>
                    {outcomeLabel(integration.last_connection_outcome)}
                  </span>
                  {integration.resource_discovery_supported && <span className="badge badge-neutral">resource discovery supported</span>}
                </div>
                {integration.last_health_checked_at && (
                  <p className="muted">Last checked: {new Date(integration.last_health_checked_at).toLocaleString()}</p>
                )}
              </div>
            );
          })}
          {!data.integrations.length && !data.plannedAdapters.length && <p className="muted">No integrations registered yet.</p>}
          {data.plannedAdapters.map(function (adapter) {
            return (
              <div className="integration-card" key={"planned-" + adapter.adapter_type} style={{ opacity: 0.7 }}>
                <div className="integration-card-header">
                  <div>
                    <div className="integration-card-name">{adapter.name}</div>
                    <div className="integration-card-category">{adapter.category}</div>
                  </div>
                </div>
                <span className="badge badge-neutral">planned -- not implemented</span>
                <p className="muted">{adapter.description}</p>
              </div>
            );
          })}
        </div>
      )}

      {showAdd && <AddIntegrationModal api={api} onClose={function () { setShowAdd(false); }} onSaved={function () { setShowAdd(false); load(true); }} />}
    </React.Fragment>
  );
}

// ---------------------------------------------------------------------------
// Add Integration wizard: pick adapter type -> non-secret config -> validate
// -> register (always starts disabled -- "enable only after required
// validation succeeds" is enforced by the backend's enable endpoint, not
// just this form's UX).
// ---------------------------------------------------------------------------
function AddIntegrationModal(props) {
  var api = props.api;
  var onClose = props.onClose;
  var onSaved = props.onSaved;

  var adaptersState = React.useState({ loading: true, error: null, adapters: [] });
  var adapters = adaptersState[0];
  var setAdapters = adaptersState[1];
  var formState = React.useState({
    adapter_type: "", integration_id: "", display_name: "", environment: "",
    subscription_id: "", resource_group: "",
  });
  var form = formState[0];
  var setForm = formState[1];
  var validationState = React.useState(null); // {valid, errors}
  var validation = validationState[0];
  var setValidation = validationState[1];
  var saveState = React.useState({ saving: false, validating: false, error: null });
  var save = saveState[0];
  var setSave = saveState[1];

  React.useEffect(function () {
    api.listAzureAdapters()
      .then(function (r) { setAdapters({ loading: false, error: null, adapters: r.adapters }); })
      .catch(function (err) { setAdapters({ loading: false, error: err.message, adapters: [] }); });
  }, []);

  function setField(key, value) { setForm(function (f) { return Object.assign({}, f, { [key]: value }); }); }

  var selectedAdapter = adapters.adapters.filter(function (a) { return a.adapter_type === form.adapter_type; })[0] || null;
  var supportsDiscovery = selectedAdapter && selectedAdapter.capabilities.resource_discovery;

  function handleValidate() {
    if (!form.adapter_type) return;
    setSave(function (s) { return Object.assign({}, s, { validating: true, error: null }); });
    api.validateAzureIntegrationConfig(form.adapter_type)
      .then(function (r) { setValidation(r); setSave(function (s) { return Object.assign({}, s, { validating: false }); }); })
      .catch(function (err) { setSave({ saving: false, validating: false, error: err.message }); });
  }

  function handleSubmit(e) {
    e.preventDefault();
    var config = {};
    if (form.subscription_id) config.subscription_id = form.subscription_id;
    if (form.resource_group) config.resource_group = form.resource_group;
    setSave({ saving: true, validating: false, error: null });
    api.registerAzureIntegration(form.integration_id, form.adapter_type, form.display_name, form.environment, config)
      .then(onSaved)
      .catch(function (err) { setSave({ saving: false, validating: false, error: err.message }); });
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <form className="modal-card" onClick={function (e) { e.stopPropagation(); }} onSubmit={handleSubmit}>
        <div className="modal-header">
          <h3>Add Azure Integration</h3>
          <button type="button" className="modal-close-btn" onClick={onClose} aria-label="Close">×</button>
        </div>

        {adapters.error && <div className="banner-error-inline">{adapters.error}</div>}

        <label className="login-field">
          Adapter type
          <select value={form.adapter_type} onChange={function (e) { setField("adapter_type", e.target.value); setValidation(null); }} required>
            <option value="" disabled>Select a service…</option>
            {adapters.adapters.map(function (a) {
              return (
                <option key={a.adapter_type} value={a.adapter_type} disabled={a.status === "planned"}>
                  {a.name} ({a.category}){a.status === "planned" ? " -- planned, not available" : ""}
                </option>
              );
            })}
          </select>
        </label>

        {selectedAdapter && selectedAdapter.status === "planned" && (
          <div className="banner banner-error">
            {selectedAdapter.description} This adapter is planned but not implemented in this repository --
            registration is not available.
          </div>
        )}
        {selectedAdapter && selectedAdapter.status !== "planned" && (
          <React.Fragment>
            <p className="muted">{selectedAdapter.description}</p>
            <p className="muted">
              Required secret(s) ({selectedAdapter.config_schema.filter(function (f) { return f.secret; }).map(function (f) { return f.key; }).join(", ") || "none"})
              must already be set via your .env file or Key Vault -- this form never accepts a secret value directly.
            </p>
          </React.Fragment>
        )}

        <label className="login-field">
          Integration ID
          <input type="text" value={form.integration_id} onChange={function (e) { setField("integration_id", e.target.value); }} required placeholder="e.g. cosmos_db_prod" />
        </label>
        <label className="login-field">
          Display name
          <input type="text" value={form.display_name} onChange={function (e) { setField("display_name", e.target.value); }} required />
        </label>
        <label className="login-field">
          Environment
          <input type="text" value={form.environment} onChange={function (e) { setField("environment", e.target.value); }} required placeholder="e.g. production" />
        </label>

        {supportsDiscovery && (
          <React.Fragment>
            <p className="muted">Optional -- only needed for this adapter's real Azure Resource Manager resource discovery:</p>
            <label className="login-field">
              Subscription ID
              <input type="text" value={form.subscription_id} onChange={function (e) { setField("subscription_id", e.target.value); }} />
            </label>
            <label className="login-field">
              Resource group
              <input type="text" value={form.resource_group} onChange={function (e) { setField("resource_group", e.target.value); }} />
            </label>
          </React.Fragment>
        )}

        {form.adapter_type && (
          <div className="stage-config-actions">
            <button type="button" className="btn" onClick={handleValidate} disabled={save.validating}>
              {save.validating ? "Validating…" : "Validate configuration"}
            </button>
          </div>
        )}
        {validation && (
          <div className={"banner " + (validation.valid ? "banner-success" : "banner-error")}>
            {validation.valid ? "Configuration valid -- required secrets are present." : validation.errors.join(" ")}
          </div>
        )}

        {save.error && <div className="banner-error-inline">{save.error}</div>}
        <div className="stage-config-actions">
          <button type="submit" className="btn btn-primary" disabled={save.saving || !form.adapter_type || (selectedAdapter && selectedAdapter.status === "planned")}>
            {save.saving ? "Registering…" : "Register (disabled by default)"}
          </button>
        </div>
      </form>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Service Details: Overview / Configuration / Resources / Diagnostics
// ---------------------------------------------------------------------------
function AzureIntegrationDetail(props) {
  var api = props.api;
  var permissions = props.permissions;
  var integrationId = props.integrationId;
  var onBack = props.onBack;

  var tabState = React.useState("overview"); // overview | configuration | resources | diagnostics
  var tab = tabState[0];
  var setTab = tabState[1];
  var state = React.useState({ loading: true, error: null, detail: null });
  var data = state[0];
  var setData = state[1];
  var actionState = React.useState({ busy: false, error: null, result: null });
  var action = actionState[0];
  var setAction = actionState[1];

  var canConfigRead = permissions.indexOf("azure.config.read") !== -1;
  var canManage = permissions.indexOf("azure.manage") !== -1;
  var canEnable = permissions.indexOf("azure.enable") !== -1;
  var canConfigWrite = permissions.indexOf("azure.config.write") !== -1;
  var canDiscover = permissions.indexOf("azure.resources.discover") !== -1;
  var canRegister = permissions.indexOf("azure.register") !== -1;

  function load() {
    setData(function (d) { return Object.assign({}, d, { loading: true, error: null }); });
    api.getAzureIntegration(integrationId)
      .then(function (result) { setData({ loading: false, error: null, detail: result }); })
      .catch(function (err) { setData(function (d) { return Object.assign({}, d, { loading: false, error: err.message }); }); });
  }

  React.useEffect(function () { if (canConfigRead) load(); }, [integrationId]);

  function handleCheck() {
    setAction({ busy: true, error: null, result: null });
    api.checkAzureIntegration(integrationId)
      .then(function (result) { setAction({ busy: false, error: null, result: result }); load(); })
      .catch(function (err) { setAction({ busy: false, error: err.message, result: null }); });
  }

  function handleSetEnabled(enabled) {
    setAction({ busy: true, error: null, result: null });
    api.setAzureIntegrationEnabled(integrationId, enabled)
      .then(function () { setAction({ busy: false, error: null, result: null }); load(); })
      .catch(function (err) { setAction({ busy: false, error: err.message, result: null }); });
  }

  function handleRemove() {
    if (!window.confirm("Remove this integration's CDPI registration? This does NOT delete, stop, or otherwise affect the real Azure resource (if any) -- it only removes CDPI's record of it.")) return;
    setAction({ busy: true, error: null, result: null });
    api.removeAzureIntegration(integrationId)
      .then(onBack)
      .catch(function (err) { setAction({ busy: false, error: err.message, result: null }); });
  }

  if (!canConfigRead) {
    return (
      <React.Fragment>
        <div className="toolbar"><button className="btn" onClick={onBack}>← Back</button></div>
        <div className="banner banner-error">Your account doesn't have the 'azure.config.read' permission required to view integration detail.</div>
      </React.Fragment>
    );
  }

  if (data.error) return (
    <React.Fragment>
      <div className="toolbar"><button className="btn" onClick={onBack}>← Back</button></div>
      <div className="banner banner-error">Couldn't load integration: {data.error}</div>
    </React.Fragment>
  );
  if (data.loading) return <p className="muted">Loading…</p>;

  var instance = data.detail.instance;
  var adapter = data.detail.adapter;

  return (
    <React.Fragment>
      <div className="toolbar">
        <button className="btn" onClick={onBack}>← Back</button>
        <button className="btn" onClick={load}>Refresh</button>
      </div>

      <h3 className="subsection-heading">{instance.display_name}</h3>
      <div className="integration-card-secrets">
        <span className={"badge " + (instance.enabled ? "badge-good" : "badge-neutral")}>{instance.enabled ? "enabled" : "disabled"}</span>
        <span className={"badge " + outcomeBadgeClass(instance.last_connection_outcome)}>{outcomeLabel(instance.last_connection_outcome)}</span>
        <span className="badge badge-neutral">{instance.environment}</span>
      </div>

      <div className="toolbar">
        <button className={"btn" + (tab === "overview" ? " btn-primary" : "")} onClick={function () { setTab("overview"); }}>Overview</button>
        <button className={"btn" + (tab === "configuration" ? " btn-primary" : "")} onClick={function () { setTab("configuration"); }}>Configuration</button>
        <button className={"btn" + (tab === "resources" ? " btn-primary" : "")} onClick={function () { setTab("resources"); }}>Resources</button>
        <button className={"btn" + (tab === "diagnostics" ? " btn-primary" : "")} onClick={function () { setTab("diagnostics"); }}>Diagnostics</button>
        <button className={"btn" + (tab === "history" ? " btn-primary" : "")} onClick={function () { setTab("history"); }}>Configuration History</button>
      </div>

      {action.error && <div className="banner-error-inline">{action.error}</div>}

      {tab === "overview" && (
        <React.Fragment>
          <p className="muted">{adapter.description}</p>
          <p className="muted">Source module: <code>{adapter.source_module}</code> · Category: {adapter.category} · Auth: {(adapter.auth_methods || []).join(", ")}</p>
          <div className="stage-config-actions">
            {canManage && <button className="btn" onClick={handleCheck} disabled={action.busy}>{action.busy ? "Checking…" : "Run connection check"}</button>}
            {canEnable && !instance.enabled && <button className="btn btn-primary" onClick={function () { handleSetEnabled(true); }} disabled={action.busy}>Enable</button>}
            {canEnable && instance.enabled && <button className="btn" onClick={function () { handleSetEnabled(false); }} disabled={action.busy}>Disable</button>}
            {canRegister && <button className="btn" onClick={handleRemove} disabled={action.busy}>Remove registration</button>}
          </div>
          {action.result && (
            <div className={"banner " + (action.result.outcome === "healthy" ? "banner-success" : "banner-error")}>
              {action.result.outcome}{action.result.error_category ? " (" + action.result.error_category + ")" : ""}: {action.result.detail}
            </div>
          )}
          {!instance.enabled && !action.result && (
            <p className="muted">Enabling requires at least one recorded "healthy" connection check -- run a check first.</p>
          )}
          {!action.result && instance.last_failure && (
            <div className="banner banner-error">
              Last failure ({new Date(instance.last_failure.checked_at).toLocaleString()}): {instance.last_failure.outcome}
              {instance.last_failure.error_category ? " (" + instance.last_failure.error_category + ")" : ""}: {instance.last_failure.detail}
            </div>
          )}
        </React.Fragment>
      )}

      {tab === "configuration" && (
        <AzureIntegrationConfigTab api={api} integrationId={integrationId} instance={instance} adapter={adapter}
          secretsConfigured={data.detail.secrets_configured} canWrite={canConfigWrite} onSaved={load} />
      )}

      {tab === "resources" && (
        <AzureIntegrationResourcesTab api={api} integrationId={integrationId} adapter={adapter} canDiscover={canDiscover} />
      )}

      {tab === "diagnostics" && (
        <AzureIntegrationDiagnosticsTab api={api} integrationId={integrationId} />
      )}

      {tab === "history" && (
        <AzureIntegrationHistoryTab api={api} integrationId={integrationId} canRollback={canConfigWrite} onRolledBack={load} />
      )}
    </React.Fragment>
  );
}

function AzureIntegrationHistoryTab(props) {
  var api = props.api;
  var integrationId = props.integrationId;
  var canRollback = props.canRollback;
  var onRolledBack = props.onRolledBack;

  var state = React.useState({ loading: true, error: null, history: [] });
  var data = state[0];
  var setData = state[1];
  var rollbackState = React.useState({ busy: false, error: null });
  var rollback = rollbackState[0];
  var setRollback = rollbackState[1];

  function load() {
    setData(function (d) { return Object.assign({}, d, { loading: true, error: null }); });
    api.getAzureIntegrationConfigHistory(integrationId, 50)
      .then(function (result) { setData({ loading: false, error: null, history: result.history }); })
      .catch(function (err) { setData(function (d) { return Object.assign({}, d, { loading: false, error: err.message }); }); });
  }

  React.useEffect(function () { load(); }, [integrationId]);

  function handleRollback(historyId) {
    if (!window.confirm("Roll back to this configuration snapshot? This creates a NEW config version (history is never overwritten) and will require revalidation before it can be enabled again.")) return;
    setRollback({ busy: true, error: null });
    api.rollbackAzureIntegrationConfig(integrationId, historyId)
      .then(function () { setRollback({ busy: false, error: null }); load(); onRolledBack(); })
      .catch(function (err) { setRollback({ busy: false, error: err.message }); });
  }

  if (data.error) return <div className="banner-error-inline">{data.error}</div>;
  if (data.loading) return <p className="muted">Loading…</p>;

  return (
    <React.Fragment>
      <div className="toolbar"><button className="btn" onClick={load}>Refresh</button></div>
      <p className="muted">
        Every registration, configuration change, activation/deactivation, rollback, and registration removal for
        this integration -- distinct from the Diagnostics tab's connection-check history (that's "did a live
        connection succeed"; this is "what changed and who changed it"). Never contains a secret value.
      </p>
      {rollback.error && <div className="banner-error-inline">{rollback.error}</div>}
      <div className="table-card">
        <table className="candidate-table">
          <thead><tr><th>When</th><th>Operation</th><th>Actor</th><th>Config version</th><th></th></tr></thead>
          <tbody>
            {data.history.map(function (h) {
              var canRollbackHere = canRollback && (h.operation === "registered" || h.operation === "config_updated");
              return (
                <tr key={h.id}>
                  <td>{h.recorded_at ? new Date(h.recorded_at).toLocaleString() : "—"}</td>
                  <td>{h.operation}</td>
                  <td>{h.actor}</td>
                  <td>{(h.safe_summary || {}).config_version ?? "—"}</td>
                  <td>{canRollbackHere && <button className="btn" onClick={function () { handleRollback(h.id); }} disabled={rollback.busy}>Roll back to this</button>}</td>
                </tr>
              );
            })}
            {!data.history.length && <tr><td colSpan={5} className="muted">No configuration history recorded yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </React.Fragment>
  );
}

function AzureIntegrationConfigTab(props) {
  var api = props.api;
  var integrationId = props.integrationId;
  var instance = props.instance;
  var adapter = props.adapter;
  var secretsConfigured = props.secretsConfigured;
  var canWrite = props.canWrite;
  var onSaved = props.onSaved;

  var nonSecretFields = adapter.config_schema.filter(function (f) { return !f.secret; });
  var formState = React.useState(function () {
    var initial = {};
    nonSecretFields.forEach(function (f) { initial[f.key] = (instance.config || {})[f.key] || ""; });
    return initial;
  });
  var form = formState[0];
  var setForm = formState[1];
  var saveState = React.useState({ saving: false, error: null, saved: false });
  var save = saveState[0];
  var setSave = saveState[1];

  function setField(key, value) { setForm(function (f) { return Object.assign({}, f, { [key]: value }); }); setSave({ saving: false, error: null, saved: false }); }

  function handleSave() {
    setSave({ saving: true, error: null, saved: false });
    api.updateAzureIntegrationConfig(integrationId, form, instance.config_version)
      .then(function () { setSave({ saving: false, error: null, saved: true }); onSaved(); })
      .catch(function (err) { setSave({ saving: false, error: err.message, saved: false }); });
  }

  return (
    <React.Fragment>
      <h4 className="subsection-heading">Secrets (presence only -- values are never shown or stored here)</h4>
      <div className="integration-card-secrets">
        {Object.keys(secretsConfigured).map(function (key) {
          return <span key={key} className={"badge " + (secretsConfigured[key] ? "badge-good" : "badge-bad")}>{key}</span>;
        })}
        {!Object.keys(secretsConfigured).length && <span className="muted">This adapter has no required secrets.</span>}
      </div>
      <p className="muted">Secret values are configured via your .env file or Key Vault (see secrets_loader.py) -- never through this UI or API.</p>

      {!!nonSecretFields.length && (
        <React.Fragment>
          <h4 className="subsection-heading">Non-secret configuration (config version {instance.config_version})</h4>
          {nonSecretFields.map(function (f) {
            return (
              <label key={f.key} className="login-field">
                {f.label}
                <input type="text" value={form[f.key]} disabled={!canWrite}
                  onChange={function (e) { setField(f.key, e.target.value); }} />
              </label>
            );
          })}
          {canWrite && (
            <div className="stage-config-actions">
              <button className="btn btn-primary" onClick={handleSave} disabled={save.saving}>{save.saving ? "Saving…" : "Save configuration"}</button>
            </div>
          )}
          {save.error && <div className="banner-error-inline">{save.error}</div>}
          {save.saved && <div className="banner banner-success">Saved.</div>}
        </React.Fragment>
      )}
    </React.Fragment>
  );
}

function AzureIntegrationResourcesTab(props) {
  var api = props.api;
  var integrationId = props.integrationId;
  var adapter = props.adapter;
  var canDiscover = props.canDiscover;

  var state = React.useState({ loading: false, error: null, result: null });
  var data = state[0];
  var setData = state[1];

  function handleDiscover() {
    setData({ loading: true, error: null, result: null });
    api.discoverAzureResources(integrationId)
      .then(function (result) { setData({ loading: false, error: null, result: result }); })
      .catch(function (err) { setData({ loading: false, error: err.message, result: null }); });
  }

  if (!adapter.capabilities.resource_discovery) {
    return <p className="muted">Resource discovery is not supported for this adapter type -- this repo has no azure_resource_type mapping for it. This is a stated gap, not an error.</p>;
  }

  return (
    <React.Fragment>
      {canDiscover && (
        <div className="toolbar">
          <button className="btn" onClick={handleDiscover} disabled={data.loading}>{data.loading ? "Discovering…" : "Discover resources"}</button>
        </div>
      )}
      {!canDiscover && <p className="muted">Your account doesn't have the 'azure.resources.discover' permission.</p>}
      {data.error && <div className="banner-error-inline">{data.error}</div>}
      {data.result && !data.result.supported && (
        <div className="banner banner-error">{data.result.error}</div>
      )}
      {data.result && data.result.supported && (
        data.result.error
          ? <div className="banner banner-error">{data.result.error}</div>
          : (
            <div className="table-card">
              <table className="candidate-table">
                <thead><tr><th>Name</th><th>Type</th><th>Location</th><th>Provisioning state</th></tr></thead>
                <tbody>
                  {data.result.resources.map(function (r) {
                    return <tr key={r.resource_id}><td>{r.name}</td><td>{r.type}</td><td>{r.location}</td><td>{r.provisioning_state}</td></tr>;
                  })}
                  {!data.result.resources.length && <tr><td colSpan={4} className="muted">No resources found in this subscription/resource group.</td></tr>}
                </tbody>
              </table>
            </div>
          )
      )}
      <p className="muted">
        No create/update/delete operation is implemented for any adapter in this release -- discovery is
        read-only, and the real Azure resource (if any) is never modified from this page.
      </p>
    </React.Fragment>
  );
}

function AzureIntegrationDiagnosticsTab(props) {
  var api = props.api;
  var integrationId = props.integrationId;

  var state = React.useState({ loading: true, error: null, checks: [] });
  var data = state[0];
  var setData = state[1];

  function load() {
    setData(function (d) { return Object.assign({}, d, { loading: true, error: null }); });
    api.getAzureIntegrationHealthHistory(integrationId, 50)
      .then(function (result) { setData({ loading: false, error: null, checks: result.checks }); })
      .catch(function (err) { setData(function (d) { return Object.assign({}, d, { loading: false, error: err.message }); }); });
  }

  React.useEffect(function () { load(); }, [integrationId]);

  if (data.error) return <div className="banner-error-inline">{data.error}</div>;
  if (data.loading) return <p className="muted">Loading…</p>;

  return (
    <React.Fragment>
      <div className="toolbar"><button className="btn" onClick={load}>Refresh</button></div>
      <div className="table-card">
        <table className="candidate-table">
          <thead><tr><th>Checked at</th><th>Outcome</th><th>Error category</th><th>Detail</th><th>Duration</th></tr></thead>
          <tbody>
            {data.checks.map(function (c, i) {
              return (
                <tr key={i}>
                  <td>{c.checked_at ? new Date(c.checked_at).toLocaleString() : "—"}</td>
                  <td><span className={"badge " + outcomeBadgeClass(c.outcome)}>{c.outcome}</span></td>
                  <td>{c.error_category || "—"}</td>
                  <td>{c.detail}</td>
                  <td>{c.duration_ms} ms</td>
                </tr>
              );
            })}
            {!data.checks.length && <tr><td colSpan={5} className="muted">No connection checks recorded yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </React.Fragment>
  );
}
