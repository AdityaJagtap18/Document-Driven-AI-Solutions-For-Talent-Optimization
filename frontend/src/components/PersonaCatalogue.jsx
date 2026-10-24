/**
 * Admin Panel: Persona Catalogue (GET /admin/personas).
 *
 * CDPI Product Personas doc, Section 8: one representative persona per
 * persona type, with role mapping, allowed pages, and report
 * authorization/status all computed live from the real role/page
 * registry -- see persona_catalogue.py. This page is a read-only view
 * over that data, not a separate authorization system: selecting a
 * persona here never changes what any real account can do.
 *
 * Report status uses the spec's own three-state vocabulary: "available"
 * (real data, persona's role actually holds the gate), "planned"
 * (specified, not built yet), "data_dependent" (can't be built reliably
 * until the underlying data/instrumentation exists) -- never a fabricated
 * fourth state.
 */
var STATUS_LABEL = { available: "Available", planned: "Planned", data_dependent: "Data dependent" };
var STATUS_BADGE_CLASS = { available: "badge-good", planned: "badge-neutral", data_dependent: "badge-bad" };

function PersonaCatalogue(props) {
  var api = props.api;
  var state = React.useState({ loading: true, error: null, data: null });
  var data = state[0];
  var setData = state[1];
  var selectedState = React.useState(0);
  var selected = selectedState[0];
  var setSelected = selectedState[1];

  React.useEffect(function () {
    api.getPersonaCatalogue()
      .then(function (result) { setData({ loading: false, error: null, data: result }); })
      .catch(function (err) { setData({ loading: false, error: err.message, data: null }); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (data.loading) return <div className="view"><h2>Persona Catalogue</h2><p className="muted">Loading…</p></div>;
  if (data.error) return <div className="view"><h2>Persona Catalogue</h2><div className="banner banner-error">Couldn't load the persona catalogue: {data.error}</div></div>;

  var personas = data.data.personas;
  var problems = data.data.validation_problems || [];
  var persona = personas[selected];

  return (
    <div className="view">
      <h2>Persona Catalogue</h2>
      <p className="muted">
        The 12 representative persona configurations from the CDPI Product Personas doc (Section 8).
        Each persona's allowed pages and report access below are computed live from the real role and
        page registry -- not a hand-typed list -- so this view can never drift from what that persona's
        account would actually see.
      </p>
      {problems.length > 0 && (
        <div className="banner banner-error">
          Validation found {problems.length} inconsistency(ies) between the catalogue and the live role data: {problems.join("; ")}
        </div>
      )}

      <div className="toolbar" style={{ flexWrap: "wrap" }}>
        {personas.map(function (p, i) {
          return (
            <button
              key={p.persona_id}
              className={"btn" + (i === selected ? " btn-primary" : "")}
              onClick={function () { setSelected(i); }}
            >
              {p.persona_id}
            </button>
          );
        })}
      </div>

      <div className="chart-card chart-card-wide">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12 }}>
          <div>
            <h4 style={{ marginBottom: 2 }}>{persona.demo_display_name}</h4>
            <p className="muted" style={{ margin: 0 }}>Role: {persona.role_display_name} ({persona.role_key})</p>
          </div>
          <span className={"badge " + (persona.export_permission ? "badge-good" : "badge-neutral")}>
            {persona.export_permission ? "Export allowed" : "No export"}
          </span>
        </div>
        <p style={{ marginTop: 10 }}>{persona.responsibilities}</p>
        <p className="muted" style={{ fontSize: "0.85rem" }}>Data scope: {persona.data_scope}</p>

        <div className="chip-list" style={{ marginTop: 14 }}>
          <strong style={{ width: "100%", fontSize: "0.85rem", marginBottom: 4 }}>
            Allowed pages ({persona.allowed_pages.length} of {persona.allowed_pages.length + persona.restricted_pages.length})
          </strong>
          {persona.allowed_pages.map(function (pageId) { return <span className="chip" key={pageId}>{pageId}</span>; })}
          {persona.allowed_pages.length === 0 && <span className="muted">None</span>}
        </div>

        <div style={{ marginTop: 16 }}>
          <strong style={{ fontSize: "0.85rem" }}>
            Required reports ({persona.allowed_reports.length} of {persona.allowed_reports.length + persona.restricted_reports.length} in the full catalogue authorized)
          </strong>
          <div className="table-card" style={{ marginTop: 8 }}>
            <table className="candidate-table">
              <thead>
                <tr><th>Report</th><th>Status</th><th>Note</th></tr>
              </thead>
              <tbody>
                {persona.allowed_reports.map(function (r) {
                  return (
                    <tr key={r.slug}>
                      <td>{r.name}</td>
                      <td><span className={"badge " + STATUS_BADGE_CLASS[r.status]}>{STATUS_LABEL[r.status]}</span></td>
                      <td className="muted" style={{ fontSize: "0.82rem" }}>{r.note}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
