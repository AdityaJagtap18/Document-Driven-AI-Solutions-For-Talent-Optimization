/**
 * Admin Panel: Secrets Management (GET/PUT /admin/secrets).
 *
 * An inventory of every secret this app actually reads --
 * secrets_registry.SECRET_DEFINITIONS, built by grepping real
 * get_secret()/_require_secrets() call sites in this repo, not invented
 * or copied from .env.example. Shows presence (does it currently resolve)
 * and source (Key Vault vs .env) -- NEVER a value.
 *
 * Administrators holding 'secrets.write' can set a secret's value here --
 * it is written ONLY to the repo's .env file (secrets_loader.set_env_secret()),
 * never to Cosmos or any other "ordinary configuration record". The
 * value is never echoed back by any API response: once submitted, this
 * page can only ever show whether the key is now present, never what
 * was typed. This is the one deliberate exception, in this whole app,
 * to the "never accept a secret value through an API" rule every other
 * config console (Azure Service Hub, RAG, Communications) enforces --
 * made here because .env is already this repo's own canonical secret
 * store, not a new trust boundary.
 */
function SecretsManagement(props) {
  var api = props.api;
  var permissions = props.permissions || [];
  var canWrite = permissions.indexOf("secrets.write") !== -1;
  var snapshot = CDPI_REPORT_SNAPSHOT.useReportSnapshot("secrets_management", function () { return api.listSecrets(); });
  var data = { loading: snapshot.state.loading && !snapshot.state.data, error: snapshot.state.error, data: snapshot.state.data };
  var load = snapshot.refresh;
  var filterState = React.useState("");
  var filter = filterState[0];
  var setFilter = filterState[1];
  var editState = React.useState(null); // the key currently being edited, or null
  var editingKey = editState[0];
  var setEditingKey = editState[1];
  var inputState = React.useState("");
  var inputValue = inputState[0];
  var setInputValue = inputState[1];
  var saveState = React.useState({ saving: false, error: null, savedKey: null });
  var save = saveState[0];
  var setSave = saveState[1];

  function startEdit(key) {
    setEditingKey(key);
    setInputValue("");
    setSave({ saving: false, error: null, savedKey: null });
  }

  function cancelEdit() {
    setEditingKey(null);
    setInputValue("");
  }

  function handleSave(key) {
    if (!inputValue) return;
    setSave({ saving: true, error: null, savedKey: null });
    api.setSecret(key, inputValue)
      .then(function () {
        setSave({ saving: false, error: null, savedKey: key });
        setEditingKey(null);
        setInputValue("");
        load();
      })
      .catch(function (err) { setSave({ saving: false, error: err.message, savedKey: null }); });
  }

  var secrets = (data.data && data.data.secrets) || [];
  var filtered = filter
    ? secrets.filter(function (s) { return s.key.toLowerCase().indexOf(filter.toLowerCase()) !== -1 || s.label.toLowerCase().indexOf(filter.toLowerCase()) !== -1; })
    : secrets;
  var missingRequired = secrets.filter(function (s) { return s.secret && !s.present; });

  return (
    <div className="view">
      <h2>Secrets</h2>
      <p className="muted">
        Every secret this application actually reads, whether it currently resolves, and from Key Vault or
        .env -- never the value itself. {canWrite
          ? "Setting a value below writes it ONLY to the repo's .env file -- it's never shown again after submission, and never stored anywhere else."
          : "Your account can view this inventory but not set values (requires 'secrets.write')."}
      </p>
      {window.CDPI_CONFIG.mode === "mock" && (
        <div className="banner banner-error">
          Mock mode: no real .env or Key Vault exists in this browser-only demo -- "setting" a value here
          only updates this session's in-memory presence flag, no real file is written. Switch config.js to
          mode:"real" against a deployed backend to actually write to .env.
        </div>
      )}

      <div className="toolbar">
        <button className="btn" onClick={load} disabled={data.loading}>{data.loading ? "Refreshing…" : "Refresh"}</button>
        <input type="text" placeholder="Filter by name…" value={filter} onChange={function (e) { setFilter(e.target.value); }} />
        <span className="muted">Snapshot from {snapshot.state.lastUpdated ? new Date(snapshot.state.lastUpdated).toLocaleString() : "just now"}</span>
      </div>

      {data.error && <div className="banner banner-error">Couldn't load secrets: {data.error}</div>}
      {data.loading && <p className="muted">Loading…</p>}

      {!data.loading && !data.error && (
        <React.Fragment>
          <div className="integration-card-secrets" style={{ marginBottom: 14 }}>
            <span className="badge badge-neutral">
              Key Vault: {data.data.vault_configured ? "configured (AZURE_KEY_VAULT_URL set)" : "not configured -- resolving from .env only"}
            </span>
            {!!missingRequired.length && (
              <span className="badge badge-bad">{missingRequired.length} required secret(s) missing</span>
            )}
          </div>

          <div className="table-card">
            <table className="candidate-table">
              <thead>
                <tr><th>Key</th><th>Required</th><th>Present</th><th>Source</th><th>Used by</th>{canWrite && <th></th>}</tr>
              </thead>
              <tbody>
                {filtered.map(function (s) {
                  var isEditing = editingKey === s.key;
                  return (
                    <React.Fragment key={s.key}>
                      <tr>
                        <td><code>{s.key}</code><div className="muted">{s.description}</div></td>
                        <td>{s.secret ? <span className="badge badge-neutral">secret</span> : <span className="badge badge-neutral">config</span>}</td>
                        <td><span className={"badge " + (s.present ? "badge-good" : (s.secret ? "badge-bad" : "badge-neutral"))}>{s.present ? "present" : "not present"}</span></td>
                        <td>{s.source ? <span className="badge badge-neutral">{s.source === "key_vault" ? "Key Vault" : ".env / environment"}</span> : "—"}</td>
                        <td>{s.used_by.join(", ")}</td>
                        {canWrite && (
                          <td>
                            {!isEditing && <button className="btn" onClick={function () { startEdit(s.key); }}>{s.present ? "Change" : "Set value"}</button>}
                          </td>
                        )}
                      </tr>
                      {isEditing && (
                        <tr>
                          <td colSpan={canWrite ? 6 : 5}>
                            <div className="login-field" style={{ maxWidth: 420 }}>
                              New value for {s.key}
                              <input type="password" autoFocus value={inputValue} onChange={function (e) { setInputValue(e.target.value); }} placeholder="Paste or type the value -- it will not be shown again" />
                            </div>
                            <div className="stage-config-actions">
                              <button className="btn btn-primary" onClick={function () { handleSave(s.key); }} disabled={save.saving || !inputValue}>{save.saving ? "Saving…" : "Save to .env"}</button>
                              <button className="btn" onClick={cancelEdit} disabled={save.saving}>Cancel</button>
                            </div>
                            {save.error && <div className="banner-error-inline">{save.error}</div>}
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  );
                })}
                {!filtered.length && <tr><td colSpan={canWrite ? 6 : 5} className="muted">No secrets match that filter.</td></tr>}
              </tbody>
            </table>
          </div>
          {save.savedKey && <div className="banner banner-success">Saved {save.savedKey} to .env.</div>}
        </React.Fragment>
      )}
    </div>
  );
}
