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
// `group` controls sidebar grouping only (see the grouped render in App()
// below) -- it has no effect on access; a page's real visibility is still
// decided entirely by navItemsFor()/effective_pages() below. GROUP_ORDER
// is the fixed display order of the section headers; items within a
// group render in the order they appear here.
var GROUP_ORDER = ["Main", "Reports", "Recruitment", "Administration", "AI & Observability", "Azure", "Configuration"];

var NAV_ITEMS = [
  { tab: "dashboard", label: "Dashboard", Icon: IconDashboard, pageId: "dashboard", group: "Main" },
  { tab: "pipeline", label: "Pipeline", Icon: IconPipeline, pageId: "pipeline", group: "Main" },
  { tab: "review", label: "Review Queue", Icon: IconQueue, pageId: "review_queue", group: "Main" },
  { tab: "portal", label: "Candidate Portal", Icon: IconPortal, pageId: "candidate_portal", group: "Main" },

  { tab: "reports", label: "Admin Reports", Icon: IconReports, pageId: "admin_reports", group: "Reports" },
  { tab: "recruiter-reports", label: "Recruiter Reports", Icon: IconReports, pageId: "recruiter_reports", group: "Reports" },
  { tab: "recruitment-ops", label: "Recruitment Operations", Icon: IconReports, pageId: "recruitment_ops_dashboard", group: "Reports" },
  { tab: "patch-notes", label: "Patch Notes", Icon: IconReports, pageId: "patch_notes", group: "Reports" },

  { tab: "jd-management", label: "Job Descriptions", Icon: IconQueue, pageId: "jd_management", group: "Recruitment" },

  { tab: "users", label: "Users", Icon: IconUsers, pageId: "user_management", group: "Administration" },
  { tab: "roles", label: "Roles & Permissions", Icon: IconShield, pageId: "role_management", group: "Administration" },
  { tab: "persona-catalogue", label: "Persona Catalogue", Icon: IconShield, pageId: "persona_catalogue", group: "Administration" },
  { tab: "page-access", label: "Page Access", Icon: IconShield, pageId: "page_access_management", group: "Administration" },

  { tab: "ai-observability", label: "AI Observability", Icon: IconPulse, pageId: "ai_observability", group: "AI & Observability" },
  { tab: "azure-observability-report", label: "Azure Observability Report", Icon: IconPulse, pageId: "azure_observability_report", group: "AI & Observability" },

  { tab: "azure-services", label: "Azure Service Hub", Icon: IconCloud, pageId: "azure_services", group: "Azure" },
  { tab: "azure-readiness", label: "Azure Readiness", Icon: IconCloud, pageId: "azure_readiness", group: "Azure" },

  { tab: "secrets", label: "Secrets", Icon: IconKey, pageId: "secrets_management", group: "Configuration" },
  { tab: "rag-config", label: "RAG Configuration", Icon: IconSearch, pageId: "rag_config", group: "Configuration" },
  { tab: "communications-config", label: "Communications", Icon: IconMail, pageId: "communications_config", group: "Configuration" },
];

function navItemsFor(pages) {
  return NAV_ITEMS.filter(function (item) { return pages.indexOf(item.pageId) !== -1; });
}

/** Groups an already-filtered nav-item list into [{ group, items }], in
 * GROUP_ORDER's fixed order, skipping any group this user has zero items
 * in (e.g. a role with no Azure page access gets no empty "Azure"
 * header) -- same filtering logic sidebar rendering already needs,
 * factored out so App() doesn't recompute reduce/sort details inline. */
function groupedNavItems(items) {
  return GROUP_ORDER
    .map(function (group) { return { group: group, items: items.filter(function (item) { return item.group === group; }) }; })
    .filter(function (g) { return g.items.length > 0; });
}

function App() {
  var api = window.CDPI_CONFIG.mode === "real" ? window.CDPI_REAL_API : window.CDPI_MOCK_API;
  var sessionState = React.useState(loadStoredSession);
  var session = sessionState[0]; // { token, user } | null
  var setSession = sessionState[1];
  var pagesState = React.useState([]);
  var pages = pagesState[0];
  var setPages = pagesState[1];
  var permissionsState = React.useState([]);
  var permissions = permissionsState[0];
  var setPermissions = permissionsState[1];
  var statusState = React.useState("loading"); // "loading" | "ready" | "signed-out"
  var status = statusState[0];
  var setStatus = statusState[1];
  var tabState = React.useState(null);
  var tab = tabState[0];
  var setTab = tabState[1];
  // Mobile only (<=900px, see styles.css): the sidebar becomes a
  // slide-in drawer instead of a permanently-hidden element with no way
  // to reach it -- this is what that drawer's open/closed state is.
  var sidebarOpenState = React.useState(false);
  var sidebarOpen = sidebarOpenState[0];
  var setSidebarOpen = sidebarOpenState[1];

  function signOut() {
    storeSession(null);
    api.setAuthToken(null);
    setSession(null);
    setPages([]);
    setPermissions([]);
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
        setPermissions(result.permissions || []);
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
      {sidebarOpen && <div className="sidebar-backdrop" onClick={function () { setSidebarOpen(false); }} />}
      <aside className={"sidebar" + (sidebarOpen ? " sidebar-open" : "")}>
        <div className="sidebar-brand">
          <div className="sidebar-brand-mark">C</div>
          <div className="sidebar-brand-name">CDPI</div>
          <button className="sidebar-close" onClick={function () { setSidebarOpen(false); }} aria-label="Close menu">
            <IconClose />
          </button>
        </div>

        <div>
          {groupedNavItems(navItems).map(function (section) {
            return (
              <div key={section.group} className="sidebar-section">
                <div className="sidebar-section-label">{section.group}</div>
                <nav className="sidebar-nav">
                  {section.items.map(function (item) {
                    var Icon = item.Icon;
                    return (
                      <button
                        key={item.tab}
                        className={"sidebar-link" + (item.tab === activeTab ? " sidebar-link-active" : "")}
                        onClick={function () { setTab(item.tab); setSidebarOpen(false); }}
                      >
                        <Icon /> {item.label}
                      </button>
                    );
                  })}
                </nav>
              </div>
            );
          })}
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
          <button className="topbar-menu-btn" onClick={function () { setSidebarOpen(true); }} aria-label="Open menu">
            <IconMenu />
          </button>
          <div className="topbar-title">{activeItem ? activeItem.label : "CDPI"}</div>
          <div className="topbar-right">
            <div className="topbar-mode">API: <strong>{window.CDPI_CONFIG.mode}</strong></div>
            <div className="topbar-version">v{window.CDPI_VERSION}</div>
          </div>
        </header>

        <main className="app-main">
          {activeTab === "dashboard" && <Dashboard api={api} />}
          {activeTab === "reports" && <AdminReports api={api} permissions={permissions} />}
          {activeTab === "pipeline" && <Pipeline api={api} permissions={permissions} currentUser={user} />}
          {activeTab === "review" && <ReviewQueue api={api} permissions={permissions} currentUser={user} />}
          {activeTab === "recruiter-reports" && <RecruiterReports api={api} />}
          {activeTab === "recruitment-ops" && <RecruitmentOpsDashboard api={api} />}
          {activeTab === "jd-management" && <JDManagement api={api} permissions={permissions} />}
          {activeTab === "portal" && <CandidatePortal api={api} />}
          {activeTab === "patch-notes" && <PatchNotes />}
          {activeTab === "users" && <UserManagement api={api} currentUser={user} />}
          {activeTab === "roles" && <RoleManagement api={api} />}
          {activeTab === "persona-catalogue" && <PersonaCatalogue api={api} />}
          {activeTab === "page-access" && <PageAccessManagement api={api} />}
          {activeTab === "ai-observability" && <AIObservabilityCenter api={api} />}
          {activeTab === "azure-observability-report" && <AzureObservabilityReport api={api} permissions={permissions} />}
          {activeTab === "azure-services" && <AzureServiceHub api={api} permissions={permissions} />}
          {activeTab === "azure-readiness" && <AzureReadiness api={api} permissions={permissions} />}
          {activeTab === "secrets" && <SecretsManagement api={api} permissions={permissions} />}
          {activeTab === "rag-config" && <RagConfiguration api={api} permissions={permissions} />}
          {activeTab === "communications-config" && <CommunicationsConfig api={api} permissions={permissions} />}
        </main>
      </div>
    </div>
  );
}

var root = ReactDOM.createRoot(document.getElementById("root"));
root.render(<App />);
