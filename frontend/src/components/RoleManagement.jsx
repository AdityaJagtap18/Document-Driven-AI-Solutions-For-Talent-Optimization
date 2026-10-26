/**
 * Admin Panel: Roles & Permissions. Shows the permission catalog grouped
 * by module (GET /admin/permissions, auth.PERMISSIONS) and every role's
 * grants (GET /admin/roles). System roles (Admin, Recruiter,
 * Interviewer, Candidate) are READ-ONLY here -- see
 * azure-functions/api_blueprint.py's update_role_endpoint docstring for
 * why: they're the deliberately-designed least-privilege baseline, and
 * letting them drift silently would break guarantees other features
 * depend on (e.g. Recruiter never holding screening.approve). Only a
 * custom role created here can be edited.
 */
function RoleManagement(props) {
  var api = props.api;

  var snapshot = CDPI_REPORT_SNAPSHOT.useReportSnapshot("role_management", function () {
    return Promise.all([api.listRoles(), api.listPermissions()])
      .then(function (results) { return { roles: results[0].roles, permissions: results[1].permissions }; });
  });
  var data = {
    loading: snapshot.state.loading && !snapshot.state.data, error: snapshot.state.error,
    roles: (snapshot.state.data && snapshot.state.data.roles) || [],
    permissions: (snapshot.state.data && snapshot.state.data.permissions) || {},
  };
  var load = snapshot.refresh;
  var expandedState = React.useState(null);
  var expanded = expandedState[0];
  var setExpanded = expandedState[1];
  var createOpenState = React.useState(false);
  var createOpen = createOpenState[0];
  var setCreateOpen = createOpenState[1];
  var errorMsg = React.useState(null);
  var error = errorMsg[0];
  var setError = errorMsg[1];

  var modules = {};
  Object.keys(data.permissions).forEach(function (pid) {
    var mod = data.permissions[pid].module;
    (modules[mod] = modules[mod] || []).push(pid);
  });

  return (
    <div className="view">
      <h2>Roles & Permissions</h2>
      <p className="muted">
        Admin is the top-level role (no separate Super Admin tier) and holds every permission.
        System roles below are read-only; create a custom role to grant a different combination
        of permissions.
      </p>

      <div className="toolbar">
        <button className="btn" onClick={load} disabled={data.loading}>{data.loading ? "Refreshing…" : "Refresh"}</button>
        <button className="btn btn-primary" onClick={function () { setCreateOpen(true); }}>+ New Custom Role</button>
        <span className="muted">Snapshot from {snapshot.state.lastUpdated ? new Date(snapshot.state.lastUpdated).toLocaleString() : "just now"}</span>
      </div>

      {error && <div className="banner banner-error">{error}</div>}
      {data.error && <div className="banner banner-error">Couldn't load roles: {data.error}</div>}
      {data.loading && <p className="muted">Loading…</p>}

      {!data.loading && !data.error && (
        <div className="table-card">
          <table className="candidate-table">
            <thead><tr><th>Role</th><th>Description</th><th>Permissions</th><th>Type</th><th></th></tr></thead>
            <tbody>
              {data.roles.map(function (r) {
                var isOpen = expanded === r.role_key;
                return (
                  <React.Fragment key={r.role_key}>
                    <tr>
                      <td><strong>{r.display_name}</strong></td>
                      <td className="muted">{r.description}</td>
                      <td>{r.permission_ids.length} permission(s)</td>
                      <td><span className={"badge " + (r.is_system ? "badge-neutral" : "badge-good")}>{r.is_system ? "System" : "Custom"}</span></td>
                      <td>
                        <button className="btn" onClick={function () { setExpanded(isOpen ? null : r.role_key); }}>
                          {isOpen ? "Hide" : "View"}
                        </button>
                      </td>
                    </tr>
                    {isOpen && (
                      <tr>
                        <td colSpan={5}>
                          <RolePermissionEditor
                            role={r}
                            modules={modules}
                            permissions={data.permissions}
                            onSave={r.is_system ? null : function (permissionIds) {
                              setError(null);
                              return api.updateRole(r.role_key, { permission_ids: permissionIds })
                                .then(load)
                                .catch(function (err) { setError(err.message); });
                            }}
                          />
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {createOpen && (
        <CreateRoleModal
          modules={modules}
          permissions={data.permissions}
          onClose={function () { setCreateOpen(false); }}
          onSave={function (role) {
            return api.createRole(role).then(function () { setCreateOpen(false); load(); });
          }}
        />
      )}
    </div>
  );
}

function RolePermissionEditor(props) {
  var role = props.role;
  var modules = props.modules;
  var permissions = props.permissions;
  var onSave = props.onSave; // null for system roles -- read-only
  var selectedState = React.useState(role.permission_ids.slice());
  var selected = selectedState[0];
  var setSelected = selectedState[1];
  var savingState = React.useState(false);
  var saving = savingState[0];
  var setSaving = savingState[1];

  function toggle(pid) {
    if (!onSave) return;
    setSelected(function (s) { return s.indexOf(pid) === -1 ? s.concat([pid]) : s.filter(function (p) { return p !== pid; }); });
  }

  return (
    <div className="role-permission-editor">
      {Object.keys(modules).sort().map(function (mod) {
        return (
          <div key={mod} className="permission-module-group">
            <div className="permission-module-title">{mod}</div>
            {modules[mod].map(function (pid) {
              return (
                <label key={pid} className="review-card-checkbox-label">
                  <input type="checkbox" checked={selected.indexOf(pid) !== -1} disabled={!onSave} onChange={function () { toggle(pid); }} />
                  {" "}{permissions[pid].label} <span className="muted">({pid})</span>
                </label>
              );
            })}
          </div>
        );
      })}
      {onSave && (
        <div className="stage-config-actions">
          <button
            className="btn btn-primary"
            disabled={saving}
            onClick={function () {
              setSaving(true);
              Promise.resolve(onSave(selected)).then(function () { setSaving(false); });
            }}
          >
            {saving ? "Saving…" : "Save permissions"}
          </button>
        </div>
      )}
      {!onSave && <p className="muted">System role — read-only. Create a custom role to use a different combination.</p>}
    </div>
  );
}

function CreateRoleModal(props) {
  var modules = props.modules;
  var permissions = props.permissions;
  var onClose = props.onClose;
  var onSave = props.onSave;

  var fieldsState = React.useState({ role_key: "", display_name: "", description: "", permission_ids: [] });
  var fields = fieldsState[0];
  var setFields = fieldsState[1];
  var saveState = React.useState({ saving: false, error: null });
  var save = saveState[0];
  var setSave = saveState[1];

  function setField(name, value) {
    setFields(function (f) { return Object.assign({}, f, { [name]: value }); });
  }

  function toggle(pid) {
    setFields(function (f) {
      var next = f.permission_ids.indexOf(pid) === -1 ? f.permission_ids.concat([pid]) : f.permission_ids.filter(function (p) { return p !== pid; });
      return Object.assign({}, f, { permission_ids: next });
    });
  }

  function handleSubmit(e) {
    e.preventDefault();
    setSave({ saving: true, error: null });
    onSave(fields).catch(function (err) { setSave({ saving: false, error: err.message }); });
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <form className="modal-card" onClick={function (e) { e.stopPropagation(); }} onSubmit={handleSubmit}>
        <div className="modal-header">
          <h3>New Custom Role</h3>
          <button type="button" className="modal-close-btn" onClick={onClose} aria-label="Close">×</button>
        </div>
        <label className="login-field">
          Role key (lowercase, no spaces)
          <input type="text" value={fields.role_key} onChange={function (e) { setField("role_key", e.target.value); }} required />
        </label>
        <label className="login-field">
          Display name
          <input type="text" value={fields.display_name} onChange={function (e) { setField("display_name", e.target.value); }} required />
        </label>
        <label className="login-field">
          Description
          <input type="text" value={fields.description} onChange={function (e) { setField("description", e.target.value); }} />
        </label>
        <div className="role-permission-editor">
          {Object.keys(modules).sort().map(function (mod) {
            return (
              <div key={mod} className="permission-module-group">
                <div className="permission-module-title">{mod}</div>
                {modules[mod].map(function (pid) {
                  return (
                    <label key={pid} className="review-card-checkbox-label">
                      <input type="checkbox" checked={fields.permission_ids.indexOf(pid) !== -1} onChange={function () { toggle(pid); }} />
                      {" "}{permissions[pid].label}
                    </label>
                  );
                })}
              </div>
            );
          })}
        </div>
        {save.error && <div className="banner-error-inline">{save.error}</div>}
        <div className="stage-config-actions">
          <button type="submit" className="btn btn-primary" disabled={save.saving}>{save.saving ? "Creating…" : "Create role"}</button>
        </div>
      </form>
    </div>
  );
}
