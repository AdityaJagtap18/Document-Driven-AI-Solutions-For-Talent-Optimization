/**
 * Real login -- calls POST /api/auth/login (azure-functions/api_blueprint.py's
 * login_endpoint, real/mock mirror in apiClient.js/mockApiClient.js) and the
 * server decides who you are and what you can do. This REPLACES the old
 * client-side-only demo check: there is no hardcoded account list in this
 * file anymore, and no role selector anywhere else in the app -- every
 * screen the signed-in user can reach is driven by the `permissions` array
 * the backend returns on login/session-restore (see App.jsx), never by
 * anything this file decides on its own.
 *
 * Mock-mode demo accounts (see mockApiClient.js's MOCK_USERS, same
 * password-hashing-free caveat documented there) are shown below only as a
 * convenience for exploring this app without a deployed Azure Functions
 * backend -- switching config.js to mode:"real" against a real API makes
 * this the actual login screen with no caveats.
 */
function Login(props) {
  var api = props.api;
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
  var submittingState = React.useState(false);
  var submitting = submittingState[0];
  var setSubmitting = submittingState[1];

  function handleSubmit(e) {
    e.preventDefault();
    if (!username.trim() || !password) {
      setError("Username and password are required.");
      return;
    }
    setSubmitting(true);
    setError(null);
    api.login(username.trim(), password)
      .then(function (result) {
        onLogin(result.token, result.user);
      })
      .catch(function (err) {
        setSubmitting(false);
        setError(err.message || "Sign in failed.");
      });
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

        <button type="submit" className="btn btn-primary" disabled={submitting}>
          {submitting ? "Signing in…" : "Sign in"}
        </button>

        {window.CDPI_CONFIG.mode === "mock" && (
          <p className="muted login-demo-hint">
            Mock-mode demo accounts (see mockApiClient.js's MOCK_USERS; not real auth --
            switch config.js to mode:"real" for an actual Azure Functions backend):
            admin/Admin#12345 (top-level role), recruiter/Recruiter#123,
            interviewer/Interviewer#123, candidate/Candidate#123
          </p>
        )}
        <p className="muted login-version">v{window.CDPI_VERSION}</p>
      </form>
    </div>
  );
}
