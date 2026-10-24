/**
 * Admin Panel: RAG Configuration Console (GET/POST /admin/rag-config*).
 *
 * Configures the ACTUAL resume-matching RAG pipeline in ResumeParser.py
 * -- chunking.max_chars, retrieval.top_k/similarity_threshold, and
 * generation model/temperature/max_tokens are now read from here at
 * runtime (rag_config.py), not hardcoded. Prompt templates are versioned
 * and loaded at runtime too -- editing one here changes what
 * ResumeParser.py actually sends to the LLM on its next run, once
 * activated.
 *
 * This repo's RAG use case is JD-to-resume-chunk matching for recruiter
 * summarization, not an interactive document-QA chatbot -- retrieval
 * preview and evaluation here reflect that: real retrieval against the
 * real on-disk semantic index, with an explicit "insufficient evidence"
 * state when a similarity threshold filters everything out, and no
 * invented Recall@K/NDCG/groundedness scores, since no labeled query-
 * relevance dataset exists to compute them from.
 *
 * Lifecycle: Draft -> Validate -> Activate -> Rollback (same pattern as
 * the Azure Service Hub's configuration console) -- an invalid draft can
 * never replace the active configuration, and a config change always
 * forces revalidation (a fresh "healthy" signal isn't assumed to carry
 * over) before anything new goes live.
 */
function RagConfiguration(props) {
  var api = props.api;
  var permissions = props.permissions || [];
  var tabState = React.useState("overview");
  var tab = tabState[0];
  var setTab = tabState[1];

  var canWrite = permissions.indexOf("rag.config.write") !== -1;
  var canActivate = permissions.indexOf("rag.config.activate") !== -1;
  var canPreview = permissions.indexOf("rag.preview.run") !== -1;
  var canManagePrompts = permissions.indexOf("rag.prompts.manage") !== -1;

  return (
    <div className="view">
      <h2>RAG Configuration</h2>
      <p className="muted">
        Configures the real resume-matching RAG pipeline (chunking, retrieval, generation, and prompt
        templates) used by ResumeParser.py. This repo's RAG use case matches job descriptions to resume
        chunks for recruiter summarization -- it is not a document-QA chatbot, so retrieval preview and
        evaluation below reflect that real pipeline, not an invented one.
      </p>
      {window.CDPI_CONFIG.mode === "mock" && (
        <div className="banner banner-error">
          Mock mode: no real semantic index exists in this browser-only demo -- retrieval preview honestly
          reports "no real index" rather than fabricating retrieved chunks. Switch config.js to mode:"real"
          against a deployed backend with real processed resumes to see actual retrieval.
        </div>
      )}

      <div className="toolbar">
        <button className={"btn" + (tab === "overview" ? " btn-primary" : "")} onClick={function () { setTab("overview"); }}>Overview</button>
        <button className={"btn" + (tab === "config" ? " btn-primary" : "")} onClick={function () { setTab("config"); }}>Configuration</button>
        <button className={"btn" + (tab === "prompts" ? " btn-primary" : "")} onClick={function () { setTab("prompts"); }}>Prompts</button>
        <button className={"btn" + (tab === "preview" ? " btn-primary" : "")} onClick={function () { setTab("preview"); }}>Preview &amp; Evaluation</button>
        <button className={"btn" + (tab === "history" ? " btn-primary" : "")} onClick={function () { setTab("history"); }}>History</button>
      </div>

      {tab === "overview" && <RagConfigOverview api={api} canWrite={canWrite} canActivate={canActivate} />}
      {tab === "config" && <RagConfigEditor api={api} canWrite={canWrite} canActivate={canActivate} />}
      {tab === "prompts" && <RagPromptsTab api={api} canManagePrompts={canManagePrompts} />}
      {tab === "preview" && <RagPreviewTab api={api} canPreview={canPreview} />}
      {tab === "history" && <RagConfigHistoryTab api={api} canActivate={canActivate} />}
    </div>
  );
}

function RagConfigOverview(props) {
  var api = props.api;
  var state = React.useState({ loading: true, error: null, data: null });
  var data = state[0];
  var setData = state[1];

  function load() {
    setData(function (d) { return Object.assign({}, d, { loading: true, error: null }); });
    api.getRagConfig()
      .then(function (result) { setData({ loading: false, error: null, data: result }); })
      .catch(function (err) { setData(function (d) { return Object.assign({}, d, { loading: false, error: err.message }); }); });
  }

  React.useEffect(function () { load(); }, []);

  if (data.error) return <div className="banner banner-error">Couldn't load RAG configuration: {data.error}</div>;
  if (data.loading) return <p className="muted">Loading…</p>;

  var src = data.data.source_status;
  var active = data.data.active;

  return (
    <React.Fragment>
      <div className="toolbar"><button className="btn" onClick={load}>Refresh</button></div>

      <h3 className="subsection-heading">Knowledge source status</h3>
      <p className="muted">
        Distinct facts, not one status dot: a source can be configured but not connected, connected but
        empty, or indexed but still fail a real probe query.
      </p>
      <div className="integration-card-secrets">
        <span className={"badge " + (src.configured ? "badge-good" : "badge-neutral")}>{src.configured ? "configured" : "not configured"}</span>
        <span className={"badge " + (src.connected ? "badge-good" : "badge-bad")}>{src.connected ? "connected" : "not connected"}</span>
        <span className={"badge " + (src.indexed ? "badge-good" : "badge-neutral")}>{src.indexed ? src.chunk_count + " chunks indexed" : "not indexed"}</span>
        <span className={"badge " + (src.retrievable ? "badge-good" : "badge-neutral")}>{src.retrievable ? "retrievable (probe query succeeded)" : "not confirmed retrievable"}</span>
      </div>
      {src.error && <p className="muted">{src.error}</p>}

      <h3 className="subsection-heading">Active configuration (config version {active.config_version})</h3>
      {active.note && <p className="muted">{active.note}</p>}
      <div className="table-card">
        <table className="candidate-table">
          <tbody>
            <tr><td>Chunking</td><td>max_chars = {active.chunking.max_chars}</td></tr>
            <tr><td>Retrieval</td><td>top_k = {active.retrieval.top_k}, similarity_threshold = {active.retrieval.similarity_threshold}</td></tr>
            <tr><td>Generation (chunk summarization)</td><td>{active.generation.chunk_summarization.model}, temp={active.generation.chunk_summarization.temperature}, max_tokens={active.generation.chunk_summarization.max_tokens}</td></tr>
            <tr><td>Generation (merge extraction)</td><td>{active.generation.merge_extraction.model}, temp={active.generation.merge_extraction.temperature}, max_tokens={active.generation.merge_extraction.max_tokens}</td></tr>
          </tbody>
        </table>
      </div>
      <p className="muted">
        Embedding model/dimensions are NOT configurable here -- this repo has exactly one embedding path
        (OpenAI text-embedding-3-small, 1536-dim) hardcoded in semantic-library/embeddings.py, and no second
        model is wired up to switch to. Exposing it as an editable field would misrepresent what the code
        can actually do.
      </p>
      {data.data.draft && (
        <div className="banner banner-error">
          A draft configuration (version {data.data.draft.config_version}) is pending -- go to the
          Configuration tab to review, validate, or activate it.
        </div>
      )}
    </React.Fragment>
  );
}

function RagConfigEditor(props) {
  var api = props.api;
  var canWrite = props.canWrite;
  var canActivate = props.canActivate;

  var state = React.useState({ loading: true, error: null, active: null, draft: null });
  var data = state[0];
  var setData = state[1];
  var formState = React.useState(null);
  var form = formState[0];
  var setForm = formState[1];
  var saveState = React.useState({ saving: false, error: null, validation: null });
  var save = saveState[0];
  var setSave = saveState[1];

  function load() {
    setData(function (d) { return Object.assign({}, d, { loading: true, error: null }); });
    api.getRagConfig()
      .then(function (result) {
        setData({ loading: false, error: null, active: result.active, draft: result.draft });
        setForm(result.draft || result.active);
      })
      .catch(function (err) { setData(function (d) { return Object.assign({}, d, { loading: false, error: err.message }); }); });
  }

  React.useEffect(function () { load(); }, []);

  function setField(path, value) {
    setForm(function (f) {
      var next = JSON.parse(JSON.stringify(f));
      var parts = path.split(".");
      var node = next;
      for (var i = 0; i < parts.length - 1; i++) node = node[parts[i]];
      node[parts[parts.length - 1]] = value;
      return next;
    });
    setSave({ saving: false, error: null, validation: null });
  }

  function handleValidate() {
    api.validateRagConfig({ chunking: form.chunking, retrieval: form.retrieval, generation: form.generation })
      .then(function (result) { setSave({ saving: false, error: null, validation: result }); })
      .catch(function (err) { setSave({ saving: false, error: err.message, validation: null }); });
  }

  function handleSaveDraft() {
    setSave({ saving: true, error: null, validation: null });
    api.saveRagConfigDraft({ chunking: form.chunking, retrieval: form.retrieval, generation: form.generation })
      .then(function () { setSave({ saving: false, error: null, validation: null }); load(); })
      .catch(function (err) { setSave({ saving: false, error: err.message, validation: null }); });
  }

  function handleActivate(acknowledgeReindexImpact) {
    if (!acknowledgeReindexImpact && !window.confirm("Activate this configuration? It will replace what ResumeParser.py reads on its next run. The previous configuration stays in history and can be rolled back.")) return;
    setSave({ saving: true, error: null, validation: null });
    api.activateRagConfig(acknowledgeReindexImpact)
      .then(function () { setSave({ saving: false, error: null, validation: null }); load(); })
      .catch(function (err) {
        // A 409 here specifically means chunking changed -- existing embeddings
        // won't reflect it until resumes are reprocessed. Ask for a SECOND,
        // more specific confirmation describing that impact before retrying
        // with acknowledge_reindex_impact -- never silently retry.
        if (err.status === 409 && /reindex/i.test(err.message)) {
          if (window.confirm(err.message + "\n\nActivate anyway?")) {
            handleActivate(true);
            return;
          }
          setSave({ saving: false, error: null, validation: null });
          return;
        }
        setSave({ saving: false, error: err.message, validation: null });
      });
  }

  if (data.error) return <div className="banner banner-error">{data.error}</div>;
  if (data.loading || !form) return <p className="muted">Loading…</p>;

  return (
    <React.Fragment>
      <p className="muted">
        Editing here creates/updates a DRAFT -- the active configuration is never changed until you
        explicitly activate. Changing chunking may require existing resumes to be reprocessed to reflect
        the new chunk boundaries; this page does not trigger that automatically.
      </p>

      <h4 className="subsection-heading">Chunking</h4>
      <label className="login-field">
        Max characters per chunk (200-5000)
        <input type="number" value={form.chunking.max_chars} disabled={!canWrite}
          onChange={function (e) { setField("chunking.max_chars", Number(e.target.value)); }} />
      </label>

      <h4 className="subsection-heading">Retrieval</h4>
      <label className="login-field">
        Top-K chunks (1-50)
        <input type="number" value={form.retrieval.top_k} disabled={!canWrite}
          onChange={function (e) { setField("retrieval.top_k", Number(e.target.value)); }} />
      </label>
      <label className="login-field">
        Similarity threshold (0.0-1.0) -- chunks below this are dropped, not summarized
        <input type="number" step="0.05" value={form.retrieval.similarity_threshold} disabled={!canWrite}
          onChange={function (e) { setField("retrieval.similarity_threshold", Number(e.target.value)); }} />
      </label>
      <p className="muted">
        No hybrid/keyword search or reranking is exposed here -- this repo's retrieval engine only
        implements cosine-similarity vector search. Listing options it doesn't support would be misleading.
      </p>

      <h4 className="subsection-heading">Generation -- chunk summarization step</h4>
      <label className="login-field">Model<input type="text" value={form.generation.chunk_summarization.model} disabled={!canWrite} onChange={function (e) { setField("generation.chunk_summarization.model", e.target.value); }} /></label>
      <label className="login-field">Temperature (0.0-2.0)<input type="number" step="0.1" value={form.generation.chunk_summarization.temperature} disabled={!canWrite} onChange={function (e) { setField("generation.chunk_summarization.temperature", Number(e.target.value)); }} /></label>
      <label className="login-field">Max tokens (50-4000)<input type="number" value={form.generation.chunk_summarization.max_tokens} disabled={!canWrite} onChange={function (e) { setField("generation.chunk_summarization.max_tokens", Number(e.target.value)); }} /></label>

      <h4 className="subsection-heading">Generation -- merge/structured-extraction step</h4>
      <label className="login-field">Model<input type="text" value={form.generation.merge_extraction.model} disabled={!canWrite} onChange={function (e) { setField("generation.merge_extraction.model", e.target.value); }} /></label>
      <label className="login-field">Temperature (0.0-2.0)<input type="number" step="0.1" value={form.generation.merge_extraction.temperature} disabled={!canWrite} onChange={function (e) { setField("generation.merge_extraction.temperature", Number(e.target.value)); }} /></label>
      <label className="login-field">Max tokens (50-4000)<input type="number" value={form.generation.merge_extraction.max_tokens} disabled={!canWrite} onChange={function (e) { setField("generation.merge_extraction.max_tokens", Number(e.target.value)); }} /></label>

      {canWrite && (
        <div className="stage-config-actions">
          <button className="btn" onClick={handleValidate}>Validate</button>
          <button className="btn" onClick={handleSaveDraft} disabled={save.saving}>{save.saving ? "Saving…" : "Save draft"}</button>
          {canActivate && data.draft && <button className="btn btn-primary" onClick={function () { handleActivate(false); }} disabled={save.saving}>Activate draft</button>}
        </div>
      )}
      {save.validation && (
        <div className={"banner " + (save.validation.valid ? "banner-success" : "banner-error")}>
          {save.validation.valid ? "Configuration valid." : save.validation.errors.join(" ")}
        </div>
      )}
      {save.error && <div className="banner-error-inline">{save.error}</div>}
    </React.Fragment>
  );
}

function RagPromptsTab(props) {
  var api = props.api;
  var canManagePrompts = props.canManagePrompts;

  var state = React.useState({ loading: true, error: null, prompts: null });
  var data = state[0];
  var setData = state[1];
  var editState = React.useState({}); // prompt_id -> draft template text being edited
  var edits = editState[0];
  var setEdits = editState[1];
  var actionState = React.useState({ busy: false, error: null });
  var action = actionState[0];
  var setAction = actionState[1];

  function load() {
    setData(function (d) { return Object.assign({}, d, { loading: true, error: null }); });
    api.listRagPrompts()
      .then(function (result) { setData({ loading: false, error: null, prompts: result.prompts }); })
      .catch(function (err) { setData(function (d) { return Object.assign({}, d, { loading: false, error: err.message }); }); });
  }

  React.useEffect(function () { load(); }, []);

  function handleCreateDraft(promptId) {
    var template = edits[promptId];
    if (!template) return;
    setAction({ busy: true, error: null });
    api.createRagPromptDraft(promptId, template)
      .then(function () { setAction({ busy: false, error: null }); setEdits(function (e) { return Object.assign({}, e, { [promptId]: undefined }); }); load(); })
      .catch(function (err) { setAction({ busy: false, error: err.message }); });
  }

  function handleActivateVersion(promptId, version) {
    if (!window.confirm("Activate prompt version " + version + "? ResumeParser.py will use this text on its next run.")) return;
    setAction({ busy: true, error: null });
    api.activateRagPromptVersion(promptId, version)
      .then(function () { setAction({ busy: false, error: null }); load(); })
      .catch(function (err) { setAction({ busy: false, error: err.message }); });
  }

  if (data.error) return <div className="banner banner-error">{data.error}</div>;
  if (data.loading) return <p className="muted">Loading…</p>;

  return (
    <React.Fragment>
      <p className="muted">
        The two real prompt templates ResumeParser.py loads at runtime -- editing and activating one here
        changes what the LLM actually sees on the next resume processed, not just documentation. Every
        version is kept (never deleted); activating a new one supersedes, never erases, the old one.
      </p>
      {action.error && <div className="banner-error-inline">{action.error}</div>}
      {Object.keys(data.prompts).map(function (promptId) {
        var versions = data.prompts[promptId].slice().sort(function (a, b) { return b.version - a.version; });
        var active = versions.filter(function (v) { return v.status === "active"; })[0];
        return (
          <div key={promptId} style={{ marginBottom: 24 }}>
            <h4 className="subsection-heading">{promptId}</h4>
            <p className="muted">Active: version {active ? active.version : "none"}</p>
            <div className="table-card">
              <table className="candidate-table">
                <thead><tr><th>Version</th><th>Status</th><th>Created by</th><th></th></tr></thead>
                <tbody>
                  {versions.map(function (v) {
                    return (
                      <tr key={v.version}>
                        <td>{v.version}</td>
                        <td><span className={"badge " + (v.status === "active" ? "badge-good" : "badge-neutral")}>{v.status}</span></td>
                        <td>{v.created_by}</td>
                        <td>{canManagePrompts && v.status !== "active" && <button className="btn" onClick={function () { handleActivateVersion(promptId, v.version); }} disabled={action.busy}>Activate</button>}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {canManagePrompts && (
              <React.Fragment>
                <label className="login-field">
                  New draft version's template text
                  <textarea rows={6} value={edits[promptId] !== undefined ? edits[promptId] : (active ? active.template : "")}
                    onChange={function (e) { setEdits(function (ed) { return Object.assign({}, ed, { [promptId]: e.target.value }); }); }} />
                </label>
                <div className="stage-config-actions">
                  <button className="btn" onClick={function () { handleCreateDraft(promptId); }} disabled={action.busy}>Save as new draft version</button>
                </div>
              </React.Fragment>
            )}
          </div>
        );
      })}
    </React.Fragment>
  );
}

function RagPreviewTab(props) {
  var api = props.api;
  var canPreview = props.canPreview;

  var formState = React.useState({ jdText: "", use: "active", includeGeneration: false });
  var form = formState[0];
  var setForm = formState[1];
  var state = React.useState({ loading: false, error: null, result: null });
  var data = state[0];
  var setData = state[1];

  function setField(key, value) { setForm(function (f) { return Object.assign({}, f, { [key]: value }); }); }

  function handleRun() {
    setData({ loading: true, error: null, result: null });
    api.previewRagConfig(form.jdText, { use: form.use, includeGeneration: form.includeGeneration })
      .then(function (result) { setData({ loading: false, error: null, result: result }); })
      .catch(function (err) { setData({ loading: false, error: err.message, result: null }); });
  }

  function handleCompare() {
    setData({ loading: true, error: null, result: null });
    api.previewRagConfig(form.jdText, { compare: true })
      .then(function (result) { setData({ loading: false, error: null, result: result }); })
      .catch(function (err) { setData({ loading: false, error: err.message, result: null }); });
  }

  function renderRetrievalResult(label, retrieval) {
    return (
      <React.Fragment>
        <h4 className="subsection-heading">{label} ({retrieval.latency_ms} ms, {retrieval.total_candidates} candidates)</h4>
        {retrieval.insufficient_evidence && (
          <div className="banner banner-error">No sufficiently relevant evidence was retrieved -- every candidate chunk was below the similarity threshold (or no index exists).</div>
        )}
        {!retrieval.insufficient_evidence && (
          <div className="table-card">
            <table className="candidate-table">
              <thead><tr><th>Resume</th><th>Chunk</th><th>Score</th></tr></thead>
              <tbody>
                {retrieval.chunks.map(function (c, i) {
                  return <tr key={i}><td>{c.metadata.resume_name}</td><td>{c.metadata.chunk_index}</td><td>{c.score.toFixed(4)}</td></tr>;
                })}
              </tbody>
            </table>
          </div>
        )}
      </React.Fragment>
    );
  }

  if (!canPreview) return <p className="muted">Your account doesn't have the 'rag.preview.run' permission.</p>;

  return (
    <React.Fragment>
      <p className="muted">
        Runs REAL retrieval against the actual on-disk semantic index -- never a simulated result. Metrics
        shown are limited to what's actually computable: retrieval scores and chunk counts from this real
        run. Recall@K, Precision@K, MRR, and NDCG are NOT shown, because they require a labeled query-
        relevance dataset this repo doesn't have -- showing them would mean inventing numbers.
      </p>
      <label className="login-field">
        Test job description text
        <textarea rows={5} value={form.jdText} onChange={function (e) { setField("jdText", e.target.value); }} />
      </label>
      <label className="login-field">
        Configuration to test
        <select value={form.use} onChange={function (e) { setField("use", e.target.value); }}>
          <option value="active">Active (live)</option>
          <option value="draft">Draft (pending)</option>
        </select>
      </label>
      <label className="review-card-checkbox-label">
        <input type="checkbox" checked={form.includeGeneration} onChange={function (e) { setField("includeGeneration", e.target.checked); }} />
        {" "}Also run generation (real LLM calls, real cost -- capped at 3 chunks)
      </label>
      <div className="stage-config-actions">
        <button className="btn btn-primary" onClick={handleRun} disabled={data.loading || !form.jdText.trim()}>{data.loading ? "Running…" : "Run preview"}</button>
        <button className="btn" onClick={handleCompare} disabled={data.loading || !form.jdText.trim()}>Compare draft vs. active</button>
      </div>

      {data.error && <div className="banner-error-inline">{data.error}</div>}

      {data.result && data.result.compare && (
        <React.Fragment>
          <p className="muted">Same test query, run against both configurations' retrieval parameters -- shows exactly how a pending draft would shift results before you activate it.</p>
          <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
            <div style={{ flex: 1, minWidth: 280 }}>{renderRetrievalResult("Active", data.result.compare.active)}</div>
            <div style={{ flex: 1, minWidth: 280 }}>
              {data.result.compare.draft
                ? renderRetrievalResult("Draft", data.result.compare.draft)
                : <p className="muted">No draft configuration exists to compare against.</p>}
            </div>
          </div>
        </React.Fragment>
      )}

      {data.result && data.result.retrieval && (
        <React.Fragment>
          {renderRetrievalResult("Retrieval", data.result.retrieval)}

          {data.result.generation && data.result.generation.error && (
            <div className="banner banner-error">{data.result.generation.error}</div>
          )}
          {data.result.generation && !data.result.generation.error && (
            <React.Fragment>
              <h4 className="subsection-heading">Generation ({data.result.generation.latency_ms} ms, {data.result.generation.total_tokens} tokens)</h4>
              {data.result.generation.truncated_note && <p className="muted">{data.result.generation.truncated_note}</p>}
              <p className="muted">Citations (chunks that fed this response):</p>
              <ul>
                {data.result.generation.citations.map(function (c, i) {
                  return <li key={i}>{c.resume_name} (chunk {c.chunk_index}, score {c.score.toFixed(4)})</li>;
                })}
              </ul>
              <pre style={{ whiteSpace: "pre-wrap" }}>{data.result.generation.final_response}</pre>
            </React.Fragment>
          )}
        </React.Fragment>
      )}
    </React.Fragment>
  );
}

function RagConfigHistoryTab(props) {
  var api = props.api;
  var canActivate = props.canActivate;

  var state = React.useState({ loading: true, error: null, history: [] });
  var data = state[0];
  var setData = state[1];
  var rollbackState = React.useState({ busy: false, error: null });
  var rollback = rollbackState[0];
  var setRollback = rollbackState[1];

  function load() {
    setData(function (d) { return Object.assign({}, d, { loading: true, error: null }); });
    api.getRagConfigHistory(50)
      .then(function (result) { setData({ loading: false, error: null, history: result.history }); })
      .catch(function (err) { setData(function (d) { return Object.assign({}, d, { loading: false, error: err.message }); }); });
  }

  React.useEffect(function () { load(); }, []);

  function handleRollback(historyId) {
    if (!window.confirm("Roll back to this activated configuration snapshot? This creates a new DRAFT (not a direct activation) -- you'll need to review and activate it explicitly.")) return;
    setRollback({ busy: true, error: null });
    api.rollbackRagConfig(historyId)
      .then(function () { setRollback({ busy: false, error: null }); load(); })
      .catch(function (err) { setRollback({ busy: false, error: err.message }); });
  }

  if (data.error) return <div className="banner-error-inline">{data.error}</div>;
  if (data.loading) return <p className="muted">Loading…</p>;

  return (
    <React.Fragment>
      <div className="toolbar"><button className="btn" onClick={load}>Refresh</button></div>
      {rollback.error && <div className="banner-error-inline">{rollback.error}</div>}
      <div className="table-card">
        <table className="candidate-table">
          <thead><tr><th>When</th><th>Operation</th><th>Actor</th><th></th></tr></thead>
          <tbody>
            {data.history.map(function (h) {
              return (
                <tr key={h.id}>
                  <td>{h.recorded_at ? new Date(h.recorded_at).toLocaleString() : "—"}</td>
                  <td>{h.operation}</td>
                  <td>{h.actor}</td>
                  <td>{canActivate && h.operation === "activated" && <button className="btn" onClick={function () { handleRollback(h.id); }} disabled={rollback.busy}>Roll back to this</button>}</td>
                </tr>
              );
            })}
            {!data.history.length && <tr><td colSpan={4} className="muted">No configuration history recorded yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </React.Fragment>
  );
}
