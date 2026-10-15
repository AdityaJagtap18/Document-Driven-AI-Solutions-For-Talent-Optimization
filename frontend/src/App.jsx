/**
 * Top-level app: role-gated single view, no nav bar to switch personas.
 * Each role renders exactly one view and nothing else -- a candidate
 * cannot click into the recruiter dashboard, a recruiter cannot click
 * into another recruiter's queue vs. the admin dashboard, etc.
 *
 * This is a lightweight DEMO login, not real authentication: picking a
 * role is the entire "login", nothing is checked against a real identity
 * store. Real auth (Entra ID / MSAL, roles from Azure AD group
 * membership) is follow-up work that needs an Azure AD App Registration
 * the user sets up themselves -- see frontend/README.md. The point of
 * this pass is fixing the actual usability bug (anyone could browse
 * every persona's view before), not pretending this is production
 * security.
 *
 * No router library (not worth the dependency for this many views with
 * no bundler) -- URL doesn't change, refresh loses your place within the
 * app, same limitation the old nav had, noted in frontend/README.md. The
 * session itself (which role you're signed in as) DOES survive a
 * refresh -- stored in localStorage, read back on load. Wrapped in
 * try/catch since localStorage can throw (private browsing, blocked
 * site data) -- a session just doesn't persist in that case, the app
 * still works.
 */
var SESSION_KEY = "cdpi_session";

function loadStoredUser() {
  try {
    var raw = localStorage.getItem(SESSION_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (e) {
    return null;
  }
}

function storeUser(user) {
  try {
    if (user) {
      localStorage.setItem(SESSION_KEY, JSON.stringify(user));
    } else {
      localStorage.removeItem(SESSION_KEY);
    }
  } catch (e) {
    // localStorage unavailable -- session just won't survive a refresh this time
  }
}

function App() {
  var api = window.CDPI_CONFIG.mode === "real" ? window.CDPI_REAL_API : window.CDPI_MOCK_API;
  var userState = React.useState(loadStoredUser);
  var user = userState[0];
  var setUser = userState[1];

  function handleLogin(loggedInUser) {
    storeUser(loggedInUser);
    setUser(loggedInUser);
  }

  function handleSignOut() {
    storeUser(null);
    setUser(null);
  }

  if (!user) {
    return <Login onLogin={handleLogin} />;
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-title">CDPI</div>
        <div className="app-user-indicator">
          {user.name} &middot; <strong>{user.roleLabel}</strong>
        </div>
        <button className="btn" onClick={handleSignOut}>Sign out</button>
        <div className="app-mode-indicator">
          API: <strong>{window.CDPI_CONFIG.mode}</strong>
        </div>
      </header>

      <main className="app-main">
        {user.view === "dashboard" && <Dashboard api={api} />}
        {user.view === "review" && <ReviewQueue api={api} />}
        {user.view === "portal" && <CandidatePortal api={api} />}
      </main>
    </div>
  );
}

var root = ReactDOM.createRoot(document.getElementById("root"));
root.render(<App />);
