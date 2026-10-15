/**
 * Top-level app: persona nav + view switching (no router library — not
 * worth the dependency for 3 views with no bundler; URL doesn't change,
 * which is a real limitation, noted in frontend/README.md).
 *
 * RBAC (Entra ID) is NOT implemented — this nav lets you switch personas
 * freely to see every view, there's no actual access control. A real
 * implementation needs an Entra ID App Registration (external Azure setup)
 * and MSAL.js — flagged as follow-up work, not faked here with a login
 * screen that doesn't check anything.
 */
function App() {
  var api = window.CDPI_CONFIG.mode === "real" ? window.CDPI_REAL_API : window.CDPI_MOCK_API;
  var state = React.useState("dashboard");
  var view = state[0];
  var setView = state[1];

  var PERSONAS = [
    { id: "dashboard", label: "Recruiter / Admin Dashboard" },
    { id: "review", label: "Human Review Queue" },
    { id: "portal", label: "Candidate Portal" },
  ];

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-title">CDPI</div>
        <nav className="app-nav">
          {PERSONAS.map(function (p) {
            return (
              <button
                key={p.id}
                className={"nav-btn" + (view === p.id ? " nav-btn-active" : "")}
                onClick={function () { setView(p.id); }}
              >
                {p.label}
              </button>
            );
          })}
        </nav>
        <div className="app-mode-indicator">
          API: <strong>{window.CDPI_CONFIG.mode}</strong>
        </div>
      </header>

      <main className="app-main">
        {view === "dashboard" && <Dashboard api={api} />}
        {view === "review" && <ReviewQueue api={api} />}
        {view === "portal" && <CandidatePortal api={api} />}
      </main>
    </div>
  );
}

var root = ReactDOM.createRoot(document.getElementById("root"));
root.render(<App />);
