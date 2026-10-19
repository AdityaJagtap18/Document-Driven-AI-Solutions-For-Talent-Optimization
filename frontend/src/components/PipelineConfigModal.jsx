/**
 * Lets a recruiter/admin edit the ordered list of hiring-process stage
 * names every candidate's StageTimelineModal timeline is built against
 * (GET/PUT /api/pipeline-config). One global list for now, not per-JD —
 * see repositories.get_pipeline_config()'s docstring for why.
 */
function PipelineConfigModal(props) {
  var api = props.api;
  var onClose = props.onClose;
  var onSaved = props.onSaved;

  var state = React.useState({ loading: true, error: null, stages: [] });
  var data = state[0];
  var setData = state[1];
  var saveState = React.useState({ saving: false, error: null });
  var save = saveState[0];
  var setSave = saveState[1];

  React.useEffect(function () {
    api.getPipelineConfig()
      .then(function (result) { setData({ loading: false, error: null, stages: result.stages }); })
      .catch(function (err) { setData(function (d) { return Object.assign({}, d, { loading: false, error: err.message }); }); });
  }, []);

  function updateStageName(index, value) {
    setData(function (d) {
      var stages = d.stages.slice();
      stages[index] = value;
      return Object.assign({}, d, { stages: stages });
    });
  }

  function moveStage(index, direction) {
    setData(function (d) {
      var stages = d.stages.slice();
      var target = index + direction;
      if (target < 0 || target >= stages.length) return d;
      var tmp = stages[index];
      stages[index] = stages[target];
      stages[target] = tmp;
      return Object.assign({}, d, { stages: stages });
    });
  }

  function removeStage(index) {
    setData(function (d) {
      var stages = d.stages.slice();
      stages.splice(index, 1);
      return Object.assign({}, d, { stages: stages });
    });
  }

  function addStage() {
    setData(function (d) { return Object.assign({}, d, { stages: d.stages.concat(["New Stage"]) }); });
  }

  function handleSave() {
    var cleaned = data.stages.map(function (s) { return s.trim(); }).filter(Boolean);
    if (!cleaned.length) {
      setSave({ saving: false, error: "At least one stage is required." });
      return;
    }
    setSave({ saving: true, error: null });
    api.updatePipelineConfig(cleaned)
      .then(function () {
        setSave({ saving: false, error: null });
        if (onSaved) onSaved(cleaned);
        onClose();
      })
      .catch(function (err) {
        setSave({ saving: false, error: err.message });
      });
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card" onClick={function (e) { e.stopPropagation(); }}>
        <div className="modal-header">
          <h3>Configure Pipeline Stages</h3>
          <button className="modal-close-btn" onClick={onClose} aria-label="Close">×</button>
        </div>
        <p className="muted">
          The ordered list every candidate's hiring-stage timeline is tracked against. Changing this
          doesn't alter any candidate's recorded history — only future stage updates are checked
          against the new list.
        </p>

        {data.loading && <p className="muted">Loading…</p>}
        {data.error && <div className="banner banner-error">{data.error}</div>}

        {!data.loading && !data.error && (
          <React.Fragment>
            <div className="stage-config-list">
              {data.stages.map(function (stage, i) {
                return (
                  <div className="stage-config-row" key={i}>
                    <input type="text" value={stage} onChange={function (e) { updateStageName(i, e.target.value); }} />
                    <button className="btn stage-config-btn" type="button" onClick={function () { moveStage(i, -1); }} disabled={i === 0}>↑</button>
                    <button className="btn stage-config-btn" type="button" onClick={function () { moveStage(i, 1); }} disabled={i === data.stages.length - 1}>↓</button>
                    <button className="btn stage-config-btn" type="button" onClick={function () { removeStage(i); }}>Remove</button>
                  </div>
                );
              })}
            </div>
            <button className="btn" type="button" onClick={addStage}>+ Add stage</button>

            {save.error && <div className="banner-error-inline">{save.error}</div>}
            <div className="stage-config-actions">
              <button className="btn btn-primary" type="button" onClick={handleSave} disabled={save.saving}>
                {save.saving ? "Saving…" : "Save"}
              </button>
            </div>
          </React.Fragment>
        )}
      </div>
    </div>
  );
}
