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

  // Visual-only: whether the password field shows plaintext, and the
  // "remember me" checkbox state. Neither changes what handleSubmit sends --
  // the real session persistence is App.jsx's localStorage-backed session,
  // unconditional on every login (see App.jsx's storeSession doc comment).
  var showPasswordState = React.useState(false);
  var showPassword = showPasswordState[0];
  var setShowPassword = showPasswordState[1];
  var rememberState = React.useState(true);
  var remember = rememberState[0];
  var setRemember = rememberState[1];

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
        <div className="login-form-panel" style={{ width: "100%" }}>
          <div className="login-card login-card-wide">
            <div className="login-card-header">
              <div className="login-card-title">Persona testing</div>
              <p className="login-card-desc">Pick a persona to sign in directly as that user, no password needed.</p>
            </div>

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
      </div>
    );
  }

  return (
    <div className="login-shell">
      <div className="login-split">
        <div className="login-brand-panel">
          <div className="login-brand-content">
            <div className="login-brand-logo">
              <div className="login-brand-logo-mark">T</div>
              <div>
                <div className="login-brand-logo-name">TalentIQ</div>
                <div className="login-brand-logo-sub">AI-POWERED TALENT INTELLIGENCE</div>
              </div>
            </div>
            <h1 className="login-brand-headline">See beyond<br /><em>the resume.</em></h1>
            <p className="login-brand-desc">
              Transform professional documents into meaningful talent insights.
            </p>
            <div className="login-feature-list">
              <div className="login-feature-item">
                <span className="login-feature-icon"><IconPulse /></span>
                AI-assisted resume scoring &amp; ranking
              </div>
              <div className="login-feature-item">
                <span className="login-feature-icon"><IconPipeline /></span>
                End-to-end applicant pipeline tracking
              </div>
              <div className="login-feature-item">
                <span className="login-feature-icon"><IconUsers /></span>
                Collaborative recruiter &amp; interviewer workflows
              </div>
            </div>
          </div>
        </div>

        <div className="login-form-panel">
          <form className="login-card" onSubmit={handleSubmit}>
            <div className="login-card-header">
              <div className="login-card-title">Welcome to TalentIQ</div>
              <p className="login-card-desc">Sign in to continue to your workspace.</p>
            </div>

            <label className="login-field">
              Username
              <div className="login-field-icon-wrap">
                <span className="login-field-icon"><IconUsers width={16} height={16} /></span>
                <input
                  type="text"
                  value={username}
                  autoComplete="username"
                  placeholder="you@company.com"
                  onChange={function (e) { setUsername(e.target.value); }}
                />
              </div>
            </label>

            <label className="login-field">
              Password
              <div className="login-field-icon-wrap">
                <span className="login-field-icon"><IconLock width={16} height={16} /></span>
                <input
                  type={showPassword ? "text" : "password"}
                  value={password}
                  autoComplete="current-password"
                  placeholder="••••••••••"
                  onChange={function (e) { setPassword(e.target.value); }}
                />
                <button
                  type="button"
                  className="login-field-toggle"
                  aria-label={showPassword ? "Hide password" : "Show password"}
                  onClick={function () { setShowPassword(!showPassword); }}
                >
                  {showPassword ? <IconEyeOff width={16} height={16} /> : <IconEye width={16} height={16} />}
                </button>
              </div>
            </label>

            <div className="login-row">
              <label className="login-remember">
                <input
                  type="checkbox"
                  checked={remember}
                  onChange={function (e) { setRemember(e.target.checked); }}
                />
                Remember me
              </label>
              <span className="login-forgot" role="button">Forgot password?</span>
            </div>

            {error && <div className="banner-error-inline">{error}</div>}

            <button type="submit" className="btn btn-primary" disabled={submitting}>
              {submitting ? "Signing in…" : "Sign In"}
            </button>

            <div className="login-demo-section">
              {demoError && <div className="banner-error-inline">{demoError}</div>}
              <div className="login-demo-divider">or try it out</div>
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
      </div>
    </div>
  );
}
