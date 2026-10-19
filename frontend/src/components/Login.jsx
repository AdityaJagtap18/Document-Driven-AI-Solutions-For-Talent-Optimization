/**
 * Lightweight role-gated login -- NOT real authentication. Credentials are
 * a hardcoded demo account list below, checked entirely client-side, so
 * anyone reading this file's source can see them. This exists to fix a
 * real usability problem (the old persona nav let anyone click into any
 * view, so a candidate could browse the recruiter dashboard) for a local
 * POC demo, not to provide actual access control.
 *
 * Real auth (Entra ID / MSAL, roles from Azure AD group membership) is
 * follow-up work that needs an Azure AD App Registration the user sets up
 * themselves -- see frontend/README.md.
 */
var DEMO_ACCOUNTS = {
  candidate: { password: "candidate123", roleLabel: "Candidate / Student", view: "portal" },
  recruiter: { password: "recruiter123", roleLabel: "Recruiter", view: "review" },
  admin: { password: "admin123", roleLabel: "Admin", view: "dashboard" },
};

function Login(props) {
  var onLogin = props.onLogin;
  var usernameState = React.useState("");
  var username = usernameState[0];
  var setUsername = usernameState[1];
  var passwordState = React.useState("");
  var password = passwordState[0];
  var setPassword = passwordState[1];
  var errorState = React.useState(null);
  var error = errorState[0];
  var setError = errorState[1];

  function handleSubmit(e) {
    e.preventDefault();
    var account = DEMO_ACCOUNTS[username.trim().toLowerCase()];
    if (!account || account.password !== password) {
      setError("Invalid username or password.");
      return;
    }
    setError(null);
    onLogin({ name: username.trim(), roleLabel: account.roleLabel, view: account.view });
  }

  return (
    <div className="login-shell">
      <form className="login-card" onSubmit={handleSubmit}>
        <div className="app-title">CDPI</div>
        <p className="muted">Sign in to continue.</p>

        <label className="login-field">
          Username
          <input
            type="text"
            value={username}
            autoComplete="username"
            onChange={function (e) { setUsername(e.target.value); }}
          />
        </label>

        <label className="login-field">
          Password
          <input
            type="password"
            value={password}
            autoComplete="current-password"
            onChange={function (e) { setPassword(e.target.value); }}
          />
        </label>

        {error && <div className="banner-error-inline">{error}</div>}

        <button type="submit" className="btn btn-primary">Sign in</button>

        <p className="muted login-demo-hint">
          Demo accounts (not real auth, see frontend/README.md): candidate/candidate123,
          recruiter/recruiter123, admin/admin123
        </p>
        <p className="muted login-version">v{window.CDPI_VERSION}</p>
      </form>
    </div>
  );
}
