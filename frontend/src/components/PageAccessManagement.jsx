/**
 * Admin Panel: Dynamic Page Access Management (GET/POST /admin/pages*).
 *
 * This app has no client-side router (no React Router, no bundler -- see
 * App.jsx's doc comment and auth.PAGES's docstring on the backend). The
 * "pages" listed here ARE this app's tabs, and the "route" column is the
 * tab key, not a URL -- there is nothing resembling `/candidates/:id` to
 * discover yet, a real architectural fact of this repo, not a gap hidden
 * from this screen.
 *
 * "Sync Pages" re-reconciles the backend's code-defined page catalog
 * (auth.PAGES, this app's route registry) against the persisted registry
 * -- the actual DISCOVERY step (see repositories.sync_pages()'s
 * docstring). A page only needs to exist in that catalog (added by a
 * developer alongside the App.jsx nav entry it describes) to show up
 * here after a sync; nobody has to create a page record by hand first.
 * A newly discovered page starts with NO access policy (deny-by-default)
 * until an admin configures one here.
 */
function PageAccessManagement(props) {
  var api = props.api;

  var state = React.useState({ loading: true, error: null, pages: [] });
  var data = state[0];
  var setData = state[1];
  var filterState = React.useState({ search: "", module: "", status: "", protection: "" });
  var filters = filterState[0];
  var setFilters = filterState[1];
  var syncState = React.useState({ syncing: false, result: null, error: null });
  var sync = syncState[0];
  var setSync = syncState[1];
  var configPageState = React.useState(null); // page_id | null
  var configPageId = configPageState[0];
  var setConfigPageId = configPageState[1];

  function load() {
    setData(function (d) { return Object.assign({}, d, { loading: true, error: null }); });
    api.listPages()
      .then(function (result) { setData({ loading: false, error: null, pages: result.pages }); })
      .catch(function (err) { setData(function (d) { return Object.assign({}, d, { loading: false, error: err.message }); }); });
  }

  React.useEffect(function () { load(); }, []);

  function handleSync() {
    setSync({ syncing: true, result: null, error: null });
    api.syncPages()
      .then(function (result) {
        setSync({ syncing: false, result: result, error: null });
        load();
      })
      .catch(function (err) { setSync({ syncing: false, result: null, error: err.message }); });
  }

  var modules = Array.from(new Set(data.pages.map(function (p) { return p.module; }))).sort();

  var filtered = data.pages.filter(function (p) {
    if (filters.search && (p.name + " " + p.route).toLowerCase().indexOf(filters.search.toLowerCase()) === -1) return false;
    if (filters.module && p.module !== filters.module) return false;
    if (filters.status === "configured" && p.discovery_status !== "configured") return false;
    if (filters.status === "unconfigured" && p.discovery_status !== "discovered") return false;
    if (filters.status === "stale" && p.discovery_status !== "stale") return false;
    if (filters.protection === "protected" && !p.is_protected) return false;
    if (filters.protection === "public" && p.is_protected) return false;
    return true;
  });

  function setFilter(name, value) {
    setFilters(function (f) { return Object.assign({}, f, { [name]: value }); });
  }

  return (
    <div className="view">
      <h2>Page Access Management</h2>
      <p className="muted">
        Every page this app has (its tabs -- there's no URL router here, see App.jsx), who can navigate to
        each one, and which pages were just discovered and still need an access policy configured.
      </p>

      <div className="toolbar">
        <input
          type="search" placeholder="Search by name or route…" value={filters.search}
          onChange={function (e) { setFilter("search", e.target.value); }}
        />
        <select value={filters.module} onChange={function (e) { setFilter("module", e.target.value); }}>
          <option value="">All modules</option>
          {modules.map(function (m) { return <option key={m} value={m}>{m}</option>; })}
        </select>
        <select value={filters.status} onChange={function (e) { setFilter("status", e.target.value); }}>
          <option value="">Any status</option>
          <option value="configured">Configured</option>
          <option value="unconfigured">Newly discovered (unconfigured)</option>
          <option value="stale">Stale</option>
        </select>
        <select value={filters.protection} onChange={function (e) { setFilter("protection", e.target.value); }}>
          <option value="">Protected + public</option>
          <option value="protected">Protected only</option>
          <option value="public">Public only</option>
        </select>
        <button className="btn" onClick={load}>Refresh</button>
        <button className="btn btn-primary" onClick={handleSync} disabled={sync.syncing}>
          {sync.syncing ? "Syncing…" : "Sync Pages"}
        </button>
      </div>

      {sync.error && <div className="banner banner-error">Sync failed: {sync.error}</div>}
      {sync.result && (
        <div className="banner banner-success">
          Sync complete -- {sync.result.new.length} new, {sync.result.updated.length} updated, {sync.result.stale.length} stale.
          {sync.result.new.length > 0 && " New pages default to denied for every role until configured below."}
        </div>
      )}
      {data.error && <div className="banner banner-error">Couldn't load pages: {data.error}</div>}
      {data.loading && <p className="muted">Loading…</p>}

      {!data.loading && !data.error && (
        <div className="table-card">
          <table className="candidate-table">
            <thead>
              <tr>
                <th>Page</th><th>Route</th><th>Module</th><th>Required permission</th>
                <th>Roles granted</th><th>Status</th><th>Last discovered</th><th></th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(function (p) {
                return (
                  <tr key={p.page_id}>
                    <td><strong>{p.name}</strong>{!p.is_protected && <span className="badge badge-neutral" style={{ marginLeft: 8 }}>Public</span>}</td>
                    <td className="muted">{p.route}</td>
                    <td>{p.module}</td>
                    <td className="muted">{p.required_permission || "— (role-gated)"}</td>
                    <td>{p.roles_granted_count === 0 ? <span className="banner-error-inline">0 roles</span> : p.roles_granted.join(", ")}</td>
                    <td>
                      <span className={"badge " + (p.discovery_status === "configured" ? "badge-good" : p.discovery_status === "stale" ? "badge-bad" : "badge-neutral")}>
                        {p.discovery_status === "discovered" ? "Needs configuration" : p.discovery_status}
                      </span>
                    </td>
                    <td className="muted">{p.last_discovered_at ? new Date(p.last_discovered_at).toLocaleString() : "—"}</td>
                    <td><button className="btn" onClick={function () { setConfigPageId(p.page_id); }}>Configure</button></td>
                  </tr>
                );
              })}
              {filtered.length === 0 && (
                <tr><td colSpan={8} className="muted">No pages match these filters.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {configPageId && (
        <PageAccessConfigModal
          api={api} pageId={configPageId}
          onClose={function () { setConfigPageId(null); }}
          onSaved={function () { setConfigPageId(null); load(); }}
        />
      )}
    </div>
  );
}

function PageAccessConfigModal(props) {
  var api = props.api;
  var pageId = props.pageId;
  var onClose = props.onClose;
  var onSaved = props.onSaved;

  var state = React.useState({ loading: true, error: null, page: null, access: [] });
  var data = state[0];
  var setData = state[1];
  var selectedState = React.useState({}); // role_key -> bool, local edits before save
  var selected = selectedState[0];
  var setSelected = selectedState[1];
  var saveState = React.useState({ saving: false, error: null });
  var save = saveState[0];
  var setSave = saveState[1];

  React.useEffect(function () {
    api.getPageAccess(pageId)
      .then(function (result) {
        var initial = {};
        result.access.forEach(function (row) { initial[row.role_key] = row.allowed; });
        setData({ loading: false, error: null, page: result.page, access: result.access });
        setSelected(initial);
      })
      .catch(function (err) { setData(function (d) { return Object.assign({}, d, { loading: false, error: err.message }); }); });
  }, [pageId]);

  function toggle(roleKey) {
    setSelected(function (s) { return Object.assign({}, s, { [roleKey]: !s[roleKey] }); });
  }

  function handleSave() {
    setSave({ saving: true, error: null });
    var policies = Object.keys(selected).map(function (roleKey) { return { role_key: roleKey, allowed: !!selected[roleKey] }; });
    api.setPageAccess(pageId, policies)
      .then(function () { onSaved(); })
      .catch(function (err) { setSave({ saving: false, error: err.message }); });
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card" onClick={function (e) { e.stopPropagation(); }}>
        <div className="modal-header">
          <h3>{data.page ? data.page.name : "Page Access"}</h3>
          <button className="modal-close-btn" onClick={onClose} aria-label="Close">×</button>
        </div>

        {data.loading && <p className="muted">Loading…</p>}
        {data.error && <div className="banner banner-error">{data.error}</div>}

        {!data.loading && !data.error && data.page && (
          <React.Fragment>
            <p className="muted">
              Route <code>{data.page.route}</code> — {data.page.description}
              {data.page.required_permission && <React.Fragment> Underlying operation permission: <code>{data.page.required_permission}</code>.</React.Fragment>}
              {" "}Page access only controls navigation to this screen -- it does not by itself grant any
              operation that screen's own data calls perform; those stay independently permission-checked.
            </p>
            {data.page.discovery_status === "discovered" && (
              <div className="banner banner-error">
                This page was just discovered and has no access policy yet -- every role is currently denied.
                Grant access below, then save.
              </div>
            )}

            <div className="stage-config-list">
              {data.access.map(function (row) {
                return (
                  <label key={row.role_key} className="review-card-checkbox-label">
                    <input type="checkbox" checked={!!selected[row.role_key]} onChange={function () { toggle(row.role_key); }} />
                    {" "}{row.role_display_name}
                  </label>
                );
              })}
            </div>

            {save.error && <div className="banner-error-inline">{save.error}</div>}
            <div className="stage-config-actions">
              <button className="btn btn-primary" onClick={handleSave} disabled={save.saving}>
                {save.saving ? "Saving…" : "Save access"}
              </button>
            </div>
          </React.Fragment>
        )}
      </div>
    </div>
  );
}
