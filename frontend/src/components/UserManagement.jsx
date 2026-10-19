/**
 * Admin Panel: User Management. Lists real accounts (GET /admin/users),
 * creates them (POST, with a bcrypt-hashed password server-side -- this
 * page only ever sends plaintext over the wire once, at creation/reset
 * time, and never displays a password hash anywhere), edits profiles,
 * enables/disables accounts, and assigns roles.
 *
 * Every action below is a real network call to a real, backend-enforced
 * endpoint (see azure-functions/api_blueprint.py's require_permission()
 * calls on each admin/users* route) -- this page itself only decides what
 * to SHOW, never what's actually ALLOWED; a disabled button here is a
 * convenience, not the security boundary (the server would refuse the
 * call either way, see the 403s surfaced below when it does).
 */
function UserManagement(props) {
  var api = props.api;
  var currentUser = props.currentUser;

  var state = React.useState({ loading: true, error: null, users: [], roles: [] });
  var data = state[0];
  var setData = state[1];
  var formState = React.useState(null); // { mode: "create"|"edit"|"roles", user } | null
  var form = formState[0];
  var setForm = formState[1];
  var actionError = React.useState(null);
  var errorMsg = actionError[0];
  var setErrorMsg = actionError[1];

  function load() {
    setData(function (d) { return Object.assign({}, d, { loading: true, error: null }); });
    return Promise.all([api.listUsers(), api.listRoles()])
      .then(function (results) {
        setData({ loading: false, error: null, users: results[0].users, roles: results[1].roles });
      })
      .catch(function (err) {
        setData(function (d) { return Object.assign({}, d, { loading: false, error: err.message }); });
      });
  }

  React.useEffect(function () { load(); }, []);

  function roleLabel(roleKey) {
    var role = data.roles.filter(function (r) { return r.role_key === roleKey; })[0];
    return role ? role.display_name : roleKey;
  }

  function handleToggleStatus(user) {
    var next = user.status === "active" ? "disabled" : "active";
    setErrorMsg(null);
    api.setUserStatus(user.id, next)
      .then(load)
      .catch(function (err) { setErrorMsg(err.message); });
  }

  return (
    <div className="view">
      <h2>User Management</h2>
      <p className="muted">
        Real accounts, real bcrypt-hashed passwords, real backend-enforced permissions -- not the old
        client-side demo login. See auth.py and azure-functions/api_blueprint.py.
      </p>

      <div className="toolbar">
        <button className="btn" onClick={load}>Refresh</button>
        <button className="btn btn-primary" onClick={function () { setForm({ mode: "create" }); }}>+ New User</button>
      </div>

      {errorMsg && <div className="banner banner-error">{errorMsg}</div>}
      {data.error && <div className="banner banner-error">Couldn't load users: {data.error}</div>}
      {data.loading && <p className="muted">Loading…</p>}

      {!data.loading && !data.error && (
        <div className="table-card">
          <table className="candidate-table">
            <thead>
              <tr>
                <th>Username</th><th>Display name</th><th>Email</th><th>Roles</th><th>Status</th><th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {data.users.map(function (u) {
                var isSelf = u.id === currentUser.id;
                return (
                  <tr key={u.id}>
                    <td>{u.username}</td>
                    <td>{u.display_name}</td>
                    <td className="muted">{u.email || "—"}</td>
                    <td>
                      {(u.role_keys || []).length
                        ? u.role_keys.map(function (k) { return <span className="badge badge-neutral" key={k}>{roleLabel(k)}</span>; })
                        : <span className="muted">No role assigned</span>}
                    </td>
                    <td><span className={"badge " + (u.status === "active" ? "badge-good" : "badge-bad")}>{u.status}</span></td>
                    <td className="review-card-actions">
                      <button className="btn" onClick={function () { setForm({ mode: "edit", user: u }); }}>Edit</button>
                      <button className="btn" onClick={function () { setForm({ mode: "roles", user: u }); }} disabled={isSelf} title={isSelf ? "You cannot change your own roles." : ""}>
                        Roles
                      </button>
                      <button
                        className={"btn " + (u.status === "active" ? "btn-reject" : "btn-approve")}
                        onClick={function () { handleToggleStatus(u); }}
                        disabled={isSelf}
                        title={isSelf ? "You cannot disable your own account." : ""}
                      >
                        {u.status === "active" ? "Disable" : "Enable"}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {form && form.mode === "create" && (
        <UserFormModal roles={data.roles} onClose={function () { setForm(null); }} onSave={function (payload) {
          return api.createUser(payload).then(function () { setForm(null); load(); });
        }} />
      )}
      {form && form.mode === "edit" && (
        <UserFormModal user={form.user} roles={data.roles} onClose={function () { setForm(null); }} onSave={function (payload) {
          return api.updateUser(form.user.id, payload).then(function () { setForm(null); load(); });
        }} />
      )}
      {form && form.mode === "roles" && (
        <UserRolesModal user={form.user} roles={data.roles} onClose={function () { setForm(null); }} onSave={function (roleKeys) {
          return api.setUserRoles(form.user.id, roleKeys).then(function () { setForm(null); load(); });
        }} />
      )}
    </div>
  );
}

function UserFormModal(props) {
  var isEdit = !!props.user;
  var roles = props.roles;
  var onClose = props.onClose;
  var onSave = props.onSave;

  var fieldsState = React.useState({
    username: props.user ? props.user.username : "",
    password: "",
    display_name: props.user ? props.user.display_name : "",
    email: props.user ? props.user.email : "",
    role_keys: props.user ? (props.user.role_keys || []) : [],
  });
  var fields = fieldsState[0];
  var setFields = fieldsState[1];
  var saveState = React.useState({ saving: false, error: null });
  var save = saveState[0];
  var setSave = saveState[1];

  function setField(name, value) {
    setFields(function (f) { return Object.assign({}, f, { [name]: value }); });
  }

  function toggleRole(roleKey) {
    setFields(function (f) {
      var next = f.role_keys.indexOf(roleKey) === -1
        ? f.role_keys.concat([roleKey])
        : f.role_keys.filter(function (k) { return k !== roleKey; });
      return Object.assign({}, f, { role_keys: next });
    });
  }

  function handleSubmit(e) {
    e.preventDefault();
    setSave({ saving: true, error: null });
    var payload = isEdit
      ? { display_name: fields.display_name, email: fields.email, password: fields.password || undefined }
      : { username: fields.username, password: fields.password, display_name: fields.display_name, email: fields.email, role_keys: fields.role_keys };
    onSave(payload).catch(function (err) { setSave({ saving: false, error: err.message }); });
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <form className="modal-card" onClick={function (e) { e.stopPropagation(); }} onSubmit={handleSubmit}>
        <div className="modal-header">
          <h3>{isEdit ? "Edit User" : "New User"}</h3>
          <button type="button" className="modal-close-btn" onClick={onClose} aria-label="Close">×</button>
        </div>

        {!isEdit && (
          <label className="login-field">
            Username
            <input type="text" value={fields.username} onChange={function (e) { setField("username", e.target.value); }} required />
          </label>
        )}
        <label className="login-field">
          {isEdit ? "Reset password (leave blank to keep current)" : "Password"}
          <input type="password" value={fields.password} onChange={function (e) { setField("password", e.target.value); }} required={!isEdit} minLength={8} />
        </label>
        <label className="login-field">
          Display name
          <input type="text" value={fields.display_name} onChange={function (e) { setField("display_name", e.target.value); }} required />
        </label>
        <label className="login-field">
          Email
          <input type="email" value={fields.email} onChange={function (e) { setField("email", e.target.value); }} />
        </label>

        {!isEdit && (
          <div className="login-field">
            Roles
            <div className="stage-config-list">
              {roles.map(function (r) {
                return (
                  <label key={r.role_key} className="review-card-checkbox-label">
                    <input type="checkbox" checked={fields.role_keys.indexOf(r.role_key) !== -1} onChange={function () { toggleRole(r.role_key); }} />
                    {" "}{r.display_name}
                  </label>
                );
              })}
            </div>
          </div>
        )}

        {save.error && <div className="banner-error-inline">{save.error}</div>}
        <div className="stage-config-actions">
          <button type="submit" className="btn btn-primary" disabled={save.saving}>{save.saving ? "Saving…" : "Save"}</button>
        </div>
      </form>
    </div>
  );
}

function UserRolesModal(props) {
  var user = props.user;
  var roles = props.roles;
  var onClose = props.onClose;
  var onSave = props.onSave;

  var selectedState = React.useState((user.role_keys || []).slice());
  var selected = selectedState[0];
  var setSelected = selectedState[1];
  var saveState = React.useState({ saving: false, error: null });
  var save = saveState[0];
  var setSave = saveState[1];

  function toggle(roleKey) {
    setSelected(function (s) {
      return s.indexOf(roleKey) === -1 ? s.concat([roleKey]) : s.filter(function (k) { return k !== roleKey; });
    });
  }

  function handleSave() {
    setSave({ saving: true, error: null });
    onSave(selected).catch(function (err) { setSave({ saving: false, error: err.message }); });
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card" onClick={function (e) { e.stopPropagation(); }}>
        <div className="modal-header">
          <h3>Roles for {user.display_name}</h3>
          <button className="modal-close-btn" onClick={onClose} aria-label="Close">×</button>
        </div>
        <div className="stage-config-list">
          {roles.map(function (r) {
            return (
              <label key={r.role_key} className="review-card-checkbox-label">
                <input type="checkbox" checked={selected.indexOf(r.role_key) !== -1} onChange={function () { toggle(r.role_key); }} />
                {" "}{r.display_name} <span className="muted">— {r.description}</span>
              </label>
            );
          })}
        </div>
        {save.error && <div className="banner-error-inline">{save.error}</div>}
        <div className="stage-config-actions">
          <button className="btn btn-primary" onClick={handleSave} disabled={save.saving}>{save.saving ? "Saving…" : "Save roles"}</button>
        </div>
      </div>
    </div>
  );
}
