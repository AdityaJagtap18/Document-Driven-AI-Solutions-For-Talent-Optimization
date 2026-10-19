/**
 * Top-level app: a sidebar + topbar shell whose nav is driven by REAL,
 * backend-issued PAGE ACCESS (see auth.py's PAGES/effective_pages() and
 * api_blueprint.py's GET /auth/me, which now returns a `pages` array
 * alongside `permissions`), not by a client-side role string and not by
 * raw operation permissions either. This is this app's "frontend route
 * guard" -- the single chokepoint every tab switch goes through (see
 * NAV_ITEMS/navItemsFor below) -- for the Dynamic Page Access Management
 * feature: an Admin can grant or revoke a role's access to, say, the
 * Pipeline page from the Page Access admin screen, and it takes effect on
 * every affected user's next GET /auth/me (next page load, or next time
 * their session is re-validated) without any frontend code changing.
 *
 * Page access is a NAVIGATION gate, not a replacement for operation-level
 * authorization -- being able to reach the Review Queue page doesn't
 * change what candidates.update actually lets you do once you're there;
 * every data call a page makes still goes through its OWN
 * require_permission() gate server-side (or its mock mirror,
 * mockApiClient.js), completely independent of page access. So even if
 * someone tampered with this file's nav logic in devtools to force a
 * hidden tab to render, the underlying API calls that tab's component
 * makes would still 401/403 on their own. This UI-level gate exists
 * purely so the right people see the right menu and manually typing a
 * route (there's no URL here, but the tab-switch equivalent) can't
 * reach a page the backend wouldn't grant -- not as the actual security
 * boundary, which is server-side.
 *
 * No router library (not worth the dependency for this many views with no
 * bundler) -- URL doesn't change, refresh loses your place within the app,
 * same limitation the old nav had, noted in frontend/README.md. The
 * session (JWT + cached user) DOES survive a refresh via localStorage, but
 * is always re-validated against GET /auth/me on load -- a revoked/expired
 * token, a role change, or a page-access policy change while logged in is
 * caught immediately rather than trusting the cached copy indefinitely.
 */
var SESSION_KEY = "cdpi_session";

function loadStoredSession() {
  try {
    var raw = localStorage.getItem(SESSION_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (e) {
    return null;
  }
}

function storeSession(session) {
  try {
    if (session) {
      localStorage.setItem(SESSION_KEY, JSON.stringify(session));
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

// Every nav item this app has, keyed by the page_id the backend's page
// registry (auth.PAGES) uses for it -- see GET /auth/me's `pages` array.
// The Candidate Portal's page_id (candidate_portal) has no underlying
// operation permission (see auth.py's Candidate role docstring) -- it's
// granted directly to the "candidate" role in the page-access policy
// seed instead, same mechanism as every other page, just a different
// role doing the granting.
var NAV_ITEMS = [
  { tab: "dashboard", label: "Dashboard", Icon: IconDashboard, pageId: "dashboard" },
  { tab: "reports", label: "Admin Reports", Icon: IconReports, pageId: "admin_reports" },
  { tab: "pipeline", label: "Pipeline", Icon: IconPipeline, pageId: "pipeline" },
  { tab: "review", label: "Review Queue", Icon: IconQueue, pageId: "review_queue" },
  { tab: "recruiter-reports", label: "Recruiter Reports", Icon: IconReports, pageId: "recruiter_reports" },
  { tab: "users", label: "Users", Icon: IconUsers, pageId: "user_management" },
  { tab: "roles", label: "Roles & Permissions", Icon: IconShield, pageId: "role_management" },
  { tab: "page-access", label: "Page Access", Icon: IconShield, pageId: "page_access_management" },
  { tab: "portal", label: "Candidate Portal", Icon: IconPortal, pageId: "candidate_portal" },
];

function navItemsFor(pages) {
  return NAV_ITEMS.filter(function (item) { return pages.indexOf(item.pageId) !== -1; });
}

function App() {
  var api = window.CDPI_CONFIG.mode === "real" ? window.CDPI_REAL_API : window.CDPI_MOCK_API;
  var sessionState = React.useState(loadStoredSession);
  var session = sessionState[0]; // { token, user } | null
  var setSession = sessionState[1];
  var pagesState = React.useState([]);
  var pages = pagesState[0];
  var setPages = pagesState[1];
  var statusState = React.useState("loading"); // "loading" | "ready" | "signed-out"
  var status = statusState[0];
  var setStatus = statusState[1];
  var tabState = React.useState(null);
  var tab = tabState[0];
  var setTab = tabState[1];

  function signOut() {
    storeSession(null);
    api.setAuthToken(null);
    setSession(null);
    setPages([]);
    setStatus("signed-out");
  }

  // On load (and whenever the session token changes), re-validate against
  // the backend rather than trusting the cached copy -- see this file's
  // doc comment above. This is also what makes a page-access policy
  // change (an admin revoking a role's access to some page) take effect
  // for an already-logged-in user -- their next getMe() (next load, or
  // next time the session is re-validated) returns the updated `pages`
  // array, no redeploy or forced logout needed.
  React.useEffect(function () {
    if (!session) {
      setStatus("signed-out");
      return;
    }
    api.setAuthToken(session.token);
    api.getMe()
      .then(function (result) {
        setPages(result.pages || []);
        setTab(function (current) { return current || (navItemsFor(result.pages || [])[0] || {}).tab || null; });
        setStatus("ready");
      })
      .catch(function () {
        signOut();
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session && session.token]);

  function handleLogin(token, user) {
    var newSession = { token: token, user: user };
    storeSession(newSession);
    setSession(newSession);
  }

  if (status === "loading") {
    return <div className="login-shell"><p className="muted">Loading…</p></div>;
  }
  if (status === "signed-out" || !session) {
    return <Login api={api} onLogin={handleLogin} />;
  }

  var user = session.user;
  var navItems = navItemsFor(pages);
  var activeItem = navItems.filter(function (item) { return item.tab === tab; })[0] || navItems[0];
  var activeTab = activeItem ? activeItem.tab : tab;

  // Signed in, but the backend granted this account access to zero pages
  // (e.g. an admin revoked every page from this user's role) -- an
  // explicit message, not a blank screen or a silent redirect loop.
  if (!activeItem) {
    return (
      <div className="login-shell">
        <div className="login-card">
          <div className="app-title">CDPI</div>
          <p className="muted">Your account doesn't currently have access to any page. Contact an administrator.</p>
          <button className="btn" onClick={signOut}>Sign out</button>
        </div>
      </div>
    );
  }

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
            <div className="sidebar-user-avatar">{initials(user.display_name || user.username)}</div>
            <div className="sidebar-user-meta">
              <div className="sidebar-user-name">{user.display_name || user.username}</div>
              <div className="sidebar-user-role">{(user.role_keys || []).join(", ") || "No role assigned"}</div>
            </div>
          </div>
          <button className="sidebar-signout" onClick={signOut}>
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
          {activeTab === "users" && <UserManagement api={api} currentUser={user} />}
          {activeTab === "roles" && <RoleManagement api={api} />}
          {activeTab === "page-access" && <PageAccessManagement api={api} />}
        </main>
      </div>
    </div>
  );
}

var root = ReactDOM.createRoot(document.getElementById("root"));
root.render(<App />);
