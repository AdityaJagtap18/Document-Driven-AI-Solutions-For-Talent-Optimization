/**
 * Top-level app: role-gated single view inside a sidebar + topbar shell
 * (redesigned per a SaaS-dashboard reference the user shared -- see
 * styles.css's top comment for what was and wasn't carried over). A
 * candidate still cannot click into the recruiter dashboard, a recruiter
 * still cannot click into the admin dashboard, etc. -- the sidebar only
 * ever lists the nav items valid for the signed-in role.
 *
 * This is a lightweight DEMO login, not real authentication: picking a
 * role is the entire "login", nothing is checked against a real identity
 * store. Real auth (Entra ID / MSAL, roles from Azure AD group
 * membership) is follow-up work that needs an Azure AD App Registration
 * the user sets up themselves -- see frontend/README.md.
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

function initials(name) {
  var parts = (name || "").trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  return (parts[0][0] + (parts[1] ? parts[1][0] : "")).toUpperCase();
}

// Every role's nav items, and which tab key each maps to. Admin is the
// only role with more than one -- every other role's sidebar has exactly
// one entry, which is the whole point of the role gate.
var NAV_BY_VIEW = {
  dashboard: [
    { tab: "dashboard", label: "Dashboard", Icon: IconDashboard },
    { tab: "reports", label: "Reports", Icon: IconReports },
    { tab: "pipeline", label: "Pipeline", Icon: IconPipeline },
  ],
  review: [
    { tab: "review", label: "Review Queue", Icon: IconQueue },
    { tab: "recruiter-reports", label: "Reports", Icon: IconReports },
  ],
  portal: [{ tab: "portal", label: "Candidate Portal", Icon: IconPortal }],
};

function App() {
  var api = window.CDPI_CONFIG.mode === "real" ? window.CDPI_REAL_API : window.CDPI_MOCK_API;
  var userState = React.useState(loadStoredUser);
  var user = userState[0];
  var setUser = userState[1];
  var tabState = React.useState("dashboard");
  var tab = tabState[0];
  var setTab = tabState[1];

  function handleLogin(loggedInUser) {
    storeUser(loggedInUser);
    setUser(loggedInUser);
    setTab(loggedInUser.view);
  }

  function handleSignOut() {
    storeUser(null);
    setUser(null);
  }

  if (!user) {
    return <Login onLogin={handleLogin} />;
  }

  var navItems = NAV_BY_VIEW[user.view] || [];
  var activeItem = navItems.filter(function (item) { return item.tab === tab; })[0] || navItems[0];
  var activeTab = activeItem ? activeItem.tab : tab;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-brand">
          <div className="sidebar-brand-mark">C</div>
          <div className="sidebar-brand-name">CDPI</div>
        </div>

        <div>
          <div className="sidebar-section-label">Main menu</div>
          <nav className="sidebar-nav">
            {navItems.map(function (item) {
              var Icon = item.Icon;
              return (
                <button
                  key={item.tab}
                  className={"sidebar-link" + (item.tab === activeTab ? " sidebar-link-active" : "")}
                  onClick={function () { setTab(item.tab); }}
                >
                  <Icon /> {item.label}
                </button>
              );
            })}
          </nav>
        </div>

        <div className="sidebar-footer">
          <div className="sidebar-user-card">
            <div className="sidebar-user-avatar">{initials(user.name)}</div>
            <div className="sidebar-user-meta">
              <div className="sidebar-user-name">{user.name}</div>
              <div className="sidebar-user-role">{user.roleLabel}</div>
            </div>
          </div>
          <button className="sidebar-signout" onClick={handleSignOut}>
            <IconLogout /> Sign out
          </button>
        </div>
      </aside>

      <div className="app-body">
        <header className="topbar">
          <div className="topbar-title">{activeItem ? activeItem.label : "CDPI"}</div>
          <div className="topbar-right">
            <div className="topbar-mode">API: <strong>{window.CDPI_CONFIG.mode}</strong></div>
            <div className="topbar-version">v{window.CDPI_VERSION}</div>
          </div>
        </header>

        <main className="app-main">
          {activeTab === "dashboard" && <Dashboard api={api} />}
          {activeTab === "reports" && <AdminReports api={api} />}
          {activeTab === "pipeline" && <Pipeline api={api} />}
          {activeTab === "review" && <ReviewQueue api={api} />}
          {activeTab === "recruiter-reports" && <RecruiterReports api={api} />}
          {activeTab === "portal" && <CandidatePortal api={api} />}
        </main>
      </div>
    </div>
  );
}

var root = ReactDOM.createRoot(document.getElementById("root"));
root.render(<App />);
