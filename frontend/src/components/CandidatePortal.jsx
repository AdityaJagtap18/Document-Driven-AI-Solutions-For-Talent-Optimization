/**
 * Candidate Self-Service Portal. This pass covers the "track status" half
 * (GET /api/candidates/by-email/{email} — the one real endpoint that
 * exists for candidates) in full. The "apply + upload docs" half is a
 * visible, clearly-labeled placeholder, not wired to a real endpoint —
 * see frontend/README.md for why (no application-submission endpoint
 * exists yet in azure-functions/api_blueprint.py; building one needs a
 * resume-upload + Blob Storage write flow that's its own real scope,
 * not something to fake just to fill the screen).
 */
function CandidatePortal(props) {
  var api = props.api;
  var state = React.useState({ email: "", result: null, error: null, loading: false, checked: false });
  var data = state[0];
  var setData = state[1];

  function checkStatus(e) {
    e.preventDefault();
    if (!data.email.trim()) return;
    setData(function (d) { return Object.assign({}, d, { loading: true, error: null, checked: false }); });
    api.getCandidateByEmail(data.email.trim())
      .then(function (result) {
        setData(function (d) { return Object.assign({}, d, { loading: false, result: result, checked: true }); });
      })
      .catch(function (err) {
        setData(function (d) { return Object.assign({}, d, { loading: false, result: null, error: err.message, checked: true }); });
      });
  }

  return (
    <div className="view">
      <h2>Candidate Portal</h2>

      <section className="portal-section">
        <h3>Check your application status</h3>
        <form className="status-form" onSubmit={checkStatus}>
          <input
            type="email"
            required
            placeholder="the email you applied with"
            value={data.email}
            onChange={function (e) {
              var value = e.target.value;
              setData(function (d) { return Object.assign({}, d, { email: value }); });
            }}
          />
          <button className="btn btn-primary" type="submit" disabled={data.loading}>
            {data.loading ? "Checking…" : "Check Status"}
          </button>
        </form>

        {data.checked && data.result && (
          <div className="status-result">
            <p>Hi {data.result.Name || "there"} — your application is currently:</p>
            <TierBadge tier={data.result.ranking_tier} />
            {data.result.ingested_at && (
              <p className="muted">Submitted {new Date(data.result.ingested_at).toLocaleDateString()}</p>
            )}
          </div>
        )}
        {data.checked && data.error && (
          <div className="banner banner-error">{data.error}</div>
        )}
      </section>

      <section className="portal-section portal-section-placeholder">
        <h3>Submit a new application</h3>
        <p className="muted">
          Not wired up yet — no application-submission API endpoint exists. Building this needs a
          real resume-upload flow (file input → Blob Storage → trigger processing), which is its
          own scope, not something to fake here. See frontend/README.md.
        </p>
        <div className="placeholder-form" aria-hidden="true">
          <input type="text" disabled placeholder="Full name" />
          <input type="email" disabled placeholder="Email" />
          <input type="file" disabled />
          <button className="btn" disabled>Submit Application</button>
        </div>
      </section>
    </div>
  );
}
