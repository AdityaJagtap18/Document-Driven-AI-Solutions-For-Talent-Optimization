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
 *
 * Demo Login / Persona Testing: a one-click "Login as Admin (Demo)" button,
 * plus a picker for all 12 representative personas (persona_catalogue.py) --
 * calls POST /auth/demo-login (ANONYMOUS, no password) and lands in the app
 * signed in as that persona, same as a normal sign-in. See that endpoint's
 * docstring in api_blueprint.py for why this is safe to leave on: it can
 * only ever resolve one of the 12 fixed, clearly-marked demo_<role_key>
 * accounts, never a real one. Can be turned off entirely server-side via
 * DEMO_LOGIN_ENABLED=false, in which case these endpoints 404 and the
 * picker below just shows a quiet "not available" message instead of
 * failing loudly -- this is a convenience feature, not something to make
 * a fuss about when it's deliberately disabled.
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

  // "form" (normal sign-in) | "personas" (the Persona Testing picker).
  var viewState = React.useState("form");
  var view = viewState[0];
  var setView = viewState[1];

  // Which persona_id is mid-login right now (so only that button shows a
  // spinner, not every button on the picker) -- null when nothing is.
  var demoBusyState = React.useState(null);
  var demoBusy = demoBusyState[0];
  var setDemoBusy = demoBusyState[1];
  var demoErrorState = React.useState(null);
  var demoError = demoErrorState[0];
  var setDemoError = demoErrorState[1];

  // Loaded once on mount -- small, cheap, and the admin quick-login button
  // (below) needs it too: the admin persona's id is "P08" in real mode but
  // just "admin" in mock mode (mockApiClient.js reuses each MOCK_USERS
  // role_key as its own persona id), so neither this file nor the button
  // may hardcode either literal -- it's always found by role_key==="admin"
  // in whatever list demoLoginOptions() actually returns.
  var personasState = React.useState(null); // null = not loaded yet, [] = loaded empty, array = loaded
  var personas = personasState[0];
  var setPersonas = personasState[1];
  var personasErrorState = React.useState(null);
  var personasError = personasErrorState[0];
  var setPersonasError = personasErrorState[1];

  React.useEffect(function () {
    api.demoLoginOptions()
      .then(function (result) { setPersonas(result.personas || []); })
      .catch(function (err) {
        // A 404 just means DEMO_LOGIN_ENABLED=false on this deployment --
        // a quiet message, not an alarming error banner.
        setPersonas([]);
        setPersonasError(err && err.status === 404 ? "Demo login is not available on this deployment." : (err.message || "Couldn't load demo personas."));
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  var adminPersona = personas && personas.filter(function (p) { return p.role_key === "admin"; })[0];

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

  function demoLoginAs(personaId) {
    setDemoBusy(personaId);
    setDemoError(null);
    api.demoLogin(personaId)
      .then(function (result) {
        onLogin(result.token, result.user);
      })
      .catch(function (err) {
        setDemoBusy(null);
        setDemoError(err.message || "Demo login failed.");
      });
  }

  function openPersonaPicker() {
    setView("personas");
  }

  if (view === "personas") {
    return (
      <div className="login-shell">
        <div className="login-card login-card-wide">
          <div className="app-title">CDPI</div>
          <p className="muted">Persona testing — pick a persona to sign in directly as that user, no password needed.</p>

          {demoError && <div className="banner-error-inline">{demoError}</div>}
          {personas === null && <p className="muted">Loading personas…</p>}
          {personasError && <div className="banner-error-inline">{personasError}</div>}

          {personas && personas.length > 0 && (
            <div className="persona-picker-grid">
              {personas.map(function (p) {
                var busy = demoBusy === p.persona_id;
                return (
                  <button
                    key={p.persona_id}
                    type="button"
                    className="persona-picker-card"
                    disabled={!!demoBusy}
                    onClick={function () { demoLoginAs(p.persona_id); }}
                  >
                    <div className="persona-picker-name">{p.display_name}</div>
                    <div className="muted persona-picker-role">{p.role_display_name}</div>
                    <div className="muted persona-picker-desc">{p.responsibilities}</div>
                    {busy && <div className="muted persona-picker-busy">Signing in…</div>}
                  </button>
                );
              })}
            </div>
          )}

          <button type="button" className="btn login-back-link" onClick={function () { setView("form"); setDemoError(null); }}>
            ← Back to sign in
          </button>
          <p className="muted login-version">v{window.CDPI_VERSION}</p>
        </div>
      </div>
    );
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

        <div className="login-demo-section">
          {demoError && <div className="banner-error-inline">{demoError}</div>}
          <button
            type="button"
            className="btn login-demo-btn"
            disabled={!!demoBusy || !adminPersona}
            onClick={function () { if (adminPersona) demoLoginAs(adminPersona.persona_id); }}
          >
            {adminPersona && demoBusy === adminPersona.persona_id ? "Signing in…" : "Login as Admin (Demo)"}
          </button>
          <button type="button" className="btn login-persona-link" onClick={openPersonaPicker}>
            Try a different persona →
          </button>
        </div>

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
