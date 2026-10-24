/**
 * Admin Panel: Communications Configuration Console
 * (GET/POST /admin/communications-config*).
 *
 * Covers the real channels this repo has code for -- Email (two
 * previously-parallel raw-SMTP scripts, now sharing credential
 * resolution via email_adapter.py) and WhatsApp (whatsapp_client.py,
 * genuinely wired into the candidate-decision notification workflow) --
 * plus the voice interview bot (voicebot/voice_bot.py), kept as a
 * SEPARATE lifecycle from communications per the spec's explicit "keep
 * bot config separate" rule. SMS is honestly labeled absent: there is
 * no SMS provider integration anywhere in this repository.
 *
 * Test messaging always requires an explicit recipient and confirmation,
 * and is clearly labeled as a test in the message itself. WhatsApp has
 * no safe test-send path (Meta's template-approval rules), so only a
 * real connectivity check (no message) is offered for it.
 */
function CommunicationsConfig(props) {
  var api = props.api;
  var permissions = props.permissions || [];
  var tabState = React.useState("overview");
  var tab = tabState[0];
  var setTab = tabState[1];

  var canWrite = permissions.indexOf("comms.config.write") !== -1;
  var canActivate = permissions.indexOf("comms.config.activate") !== -1;
  var canTest = permissions.indexOf("comms.test.send") !== -1;
  var canManageTemplates = permissions.indexOf("comms.templates.manage") !== -1;

  return (
    <div className="view">
      <h2>Communications Configuration</h2>
      <p className="muted">
        Configures the real email and WhatsApp send paths, the voice interview bot, message templates, and
        shared delivery policies this app actually uses. SMS is labeled "absent" below, honestly -- there is
        no SMS provider code anywhere in this repository.
      </p>
      {window.CDPI_CONFIG.mode === "mock" && (
        <div className="banner banner-error">
          Mock mode: no real SMTP server or WhatsApp Graph API access exists in this browser-only demo --
          every connectivity check honestly reports "unknown" and no test email is actually sent. Switch
          config.js to mode:"real" against a deployed backend to exercise the real adapters.
        </div>
      )}

      <div className="toolbar">
        <button className={"btn" + (tab === "overview" ? " btn-primary" : "")} onClick={function () { setTab("overview"); }}>Overview</button>
        <button className={"btn" + (tab === "email" ? " btn-primary" : "")} onClick={function () { setTab("email"); }}>Email</button>
        <button className={"btn" + (tab === "whatsapp" ? " btn-primary" : "")} onClick={function () { setTab("whatsapp"); }}>WhatsApp</button>
        <button className={"btn" + (tab === "sms" ? " btn-primary" : "")} onClick={function () { setTab("sms"); }}>SMS</button>
        <button className={"btn" + (tab === "bot" ? " btn-primary" : "")} onClick={function () { setTab("bot"); }}>Interview Bot</button>
        <button className={"btn" + (tab === "templates" ? " btn-primary" : "")} onClick={function () { setTab("templates"); }}>Message Templates</button>
        <button className={"btn" + (tab === "policies" ? " btn-primary" : "")} onClick={function () { setTab("policies"); }}>Delivery Policies</button>
        <button className={"btn" + (tab === "history" ? " btn-primary" : "")} onClick={function () { setTab("history"); }}>Configuration History</button>
      </div>

      {tab === "overview" && <CommsOverviewTab api={api} />}
      {tab === "email" && <CommsEmailTab api={api} canTest={canTest} />}
      {tab === "whatsapp" && <CommsWhatsappTab api={api} canTest={canTest} />}
      {tab === "sms" && <CommsSmsTab api={api} />}
      {tab === "bot" && <CommsInterviewBotTab api={api} canWrite={canWrite} canActivate={canActivate} />}
      {tab === "templates" && <CommsTemplatesTab api={api} canManageTemplates={canManageTemplates} />}
      {tab === "policies" && <CommsPoliciesTab api={api} canWrite={canWrite} canActivate={canActivate} />}
      {tab === "history" && <CommsHistoryTab api={api} canActivate={canActivate} />}
    </div>
  );
}

function outcomeBadgeClass(outcome) {
  if (outcome === "healthy") return "badge-good";
  if (outcome === "unhealthy") return "badge-bad";
  return "badge-neutral";
}

function CommsOverviewTab(props) {
  var api = props.api;
  var state = React.useState({ loading: true, error: null, data: null });
  var data = state[0];
  var setData = state[1];

  function load() {
    setData({ loading: true, error: null, data: null });
    api.getCommsConfig()
      .then(function (result) { setData({ loading: false, error: null, data: result }); })
      .catch(function (err) { setData({ loading: false, error: err.message, data: null }); });
  }

  React.useEffect(function () { load(); }, []);

  if (data.error) return <div className="banner banner-error">{data.error}</div>;
  if (data.loading) return <p className="muted">Loading…</p>;

  return (
    <React.Fragment>
      <div className="toolbar"><button className="btn" onClick={load}>Refresh</button></div>
      <div className="integration-grid">
        {data.data.channels.map(function (c) {
          return (
            <div className="integration-card" key={c.channel}>
              <div className="integration-card-header">
                <div>
                  <div className="integration-card-name">{c.category}</div>
                </div>
              </div>
              <span className={"badge " + (c.status === "supported" ? "badge-good" : "badge-neutral")}>{c.status}</span>
              <p className="muted">{c.description}</p>
            </div>
          );
        })}
      </div>
      <h3 className="subsection-heading">Active configuration (version {data.data.active.config_version})</h3>
      {data.data.active.note && <p className="muted">{data.data.active.note}</p>}
      <div className="table-card">
        <table className="candidate-table">
          <tbody>
            <tr><td>Email</td><td>{data.data.active.email.enabled ? "enabled" : "disabled"} -- sender "{data.data.active.email.sender_display_name}"</td></tr>
            <tr><td>WhatsApp</td><td>{data.data.active.whatsapp.enabled ? "enabled" : "disabled"}</td></tr>
            <tr><td>Duplicate-send window</td><td>{data.data.active.delivery_policies.duplicate_send_window_minutes} minutes</td></tr>
          </tbody>
        </table>
      </div>
      {data.data.draft && <div className="banner banner-error">A draft configuration (version {data.data.draft.config_version}) is pending activation.</div>}
    </React.Fragment>
  );
}

function CommsEmailTab(props) {
  var api = props.api;
  var canTest = props.canTest;
  var connState = React.useState({ busy: false, result: null, error: null });
  var conn = connState[0];
  var setConn = connState[1];
  var sendState = React.useState({ to: "", busy: false, result: null, error: null });
  var send = sendState[0];
  var setSend = sendState[1];

  function handleTestConnectivity() {
    setConn({ busy: true, result: null, error: null });
    api.testEmailConnectivity()
      .then(function (result) { setConn({ busy: false, result: result, error: null }); })
      .catch(function (err) { setConn({ busy: false, result: null, error: err.message }); });
  }

  function handleSendTest() {
    if (!send.to.trim()) return;
    if (!window.confirm("Send a real TEST email to " + send.to + "? It will be clearly labeled as a test message.")) return;
    setSend(function (s) { return Object.assign({}, s, { busy: true, result: null, error: null }); });
    api.sendTestEmail(send.to.trim(), "Communications Configuration test", "")
      .then(function (result) { setSend(function (s) { return Object.assign({}, s, { busy: false, result: result, error: null }); }); })
      .catch(function (err) { setSend(function (s) { return Object.assign({}, s, { busy: false, result: null, error: err.message }); }); });
  }

  return (
    <React.Fragment>
      <p className="muted">
        Raw SMTP via smtplib (email_adapter.py). Credentials resolve from your .env file/Key Vault first,
        falling back to the legacy per-script JSON config files if unset -- never shown or editable here.
      </p>
      {canTest && (
        <React.Fragment>
          <div className="stage-config-actions">
            <button className="btn" onClick={handleTestConnectivity} disabled={conn.busy}>{conn.busy ? "Testing…" : "Test connectivity"}</button>
          </div>
          {conn.error && <div className="banner-error-inline">{conn.error}</div>}
          {conn.result && (
            <div className={"banner " + (conn.result.outcome === "healthy" ? "banner-success" : "banner-error")}>
              <span className={"badge " + outcomeBadgeClass(conn.result.outcome)}>{conn.result.outcome}</span> {conn.result.detail}
            </div>
          )}

          <h4 className="subsection-heading">Send a test email</h4>
          <p className="muted">Requires an explicit recipient and confirmation. The message is always clearly labeled as a test.</p>
          <label className="login-field">
            Recipient email address
            <input type="email" value={send.to} onChange={function (e) { setSend(function (s) { return Object.assign({}, s, { to: e.target.value }); }); }} />
          </label>
          <div className="stage-config-actions">
            <button className="btn btn-primary" onClick={handleSendTest} disabled={send.busy || !send.to.trim()}>{send.busy ? "Sending…" : "Send test email"}</button>
          </div>
          {send.error && <div className="banner-error-inline">{send.error}</div>}
          {send.result && <div className="banner banner-success">Test email sent to {send.result.to}.</div>}
        </React.Fragment>
      )}
      {!canTest && <p className="muted">Your account doesn't have the 'comms.test.send' permission.</p>}
    </React.Fragment>
  );
}

function CommsWhatsappTab(props) {
  var api = props.api;
  var canTest = props.canTest;
  var connState = React.useState({ busy: false, result: null, error: null });
  var conn = connState[0];
  var setConn = connState[1];

  function handleTestConnectivity() {
    setConn({ busy: true, result: null, error: null });
    api.testWhatsappConnectivity()
      .then(function (result) { setConn({ busy: false, result: result, error: null }); })
      .catch(function (err) { setConn({ busy: false, result: null, error: err.message }); });
  }

  return (
    <React.Fragment>
      <p className="muted">
        Meta WhatsApp Business Cloud API (whatsapp_client.py) -- genuinely wired into the candidate-decision
        notification workflow. Templates are authored and approved in Meta Business Manager, not here; this
        console only maps a workflow to an approved template name (see the Delivery Policies tab).
      </p>
      <div className="banner banner-error">
        No test-message send is offered for WhatsApp -- Meta's template-approval rules mean there is no safe
        sandbox send path. Only a real connectivity check (metadata lookup, no message) is available.
      </div>
      {canTest && (
        <React.Fragment>
          <div className="stage-config-actions">
            <button className="btn" onClick={handleTestConnectivity} disabled={conn.busy}>{conn.busy ? "Testing…" : "Test connectivity"}</button>
          </div>
          {conn.error && <div className="banner-error-inline">{conn.error}</div>}
          {conn.result && (
            <div className={"banner " + (conn.result.outcome === "healthy" ? "banner-success" : "banner-error")}>
              <span className={"badge " + outcomeBadgeClass(conn.result.outcome)}>{conn.result.outcome}</span> {conn.result.detail}
            </div>
          )}
        </React.Fragment>
      )}
    </React.Fragment>
  );
}

function CommsSmsTab() {
  return (
    <div className="banner banser-error">
      <p>SMS is <strong>absent</strong> -- there is no SMS provider integration anywhere in this repository
      (no Twilio, no SNS, no provider client of any kind, not even an unprovisioned stub). Adding SMS support
      requires real development work: choosing a provider, provisioning an account, and writing a client
      module, none of which exists yet.</p>
    </div>
  );
}

function CommsInterviewBotTab(props) {
  var api = props.api;
  var canWrite = props.canWrite;
  var canActivate = props.canActivate;
  var state = React.useState({ loading: true, error: null, active: null, draft: null });
  var data = state[0];
  var setData = state[1];
  var formState = React.useState(null);
  var form = formState[0];
  var setForm = formState[1];
  var saveState = React.useState({ saving: false, error: null });
  var save = saveState[0];
  var setSave = saveState[1];

  function load() {
    setData(function (d) { return Object.assign({}, d, { loading: true, error: null }); });
    api.getInterviewBotConfig()
      .then(function (result) { setData({ loading: false, error: null, active: result.active, draft: result.draft }); setForm(result.draft || result.active); })
      .catch(function (err) { setData(function (d) { return Object.assign({}, d, { loading: false, error: err.message }); }); });
  }

  React.useEffect(function () { load(); }, []);

  function setField(key, value) { setForm(function (f) { return Object.assign({}, f, { [key]: value }); }); }

  function handleSaveDraft() {
    setSave({ saving: true, error: null });
    api.saveInterviewBotConfigDraft(form)
      .then(function () { setSave({ saving: false, error: null }); load(); })
      .catch(function (err) { setSave({ saving: false, error: err.message }); });
  }

  function handleActivate() {
    if (!window.confirm("Activate this interview bot configuration? It will replace what voice_bot.py reads on its next run.")) return;
    setSave({ saving: true, error: null });
    api.activateInterviewBotConfig()
      .then(function () { setSave({ saving: false, error: null }); load(); })
      .catch(function (err) { setSave({ saving: false, error: err.message }); });
  }

  if (data.error) return <div className="banner banner-error">{data.error}</div>;
  if (data.loading || !form) return <p className="muted">Loading…</p>;

  return (
    <React.Fragment>
      <p className="muted">
        voicebot/voice_bot.py's real, tunable parameters -- kept as a SEPARATE configuration from the
        communications channels above, per the spec's "keep bot config separate from communication
        configuration" rule. Only real fields are exposed: no arbitrary system-prompt text or code.
      </p>
      {data.active.note && <p className="muted">{data.active.note}</p>}
      <label className="login-field">Model<input type="text" value={form.model} disabled={!canWrite} onChange={function (e) { setField("model", e.target.value); }} /></label>
      <label className="login-field">Temperature (0.0-2.0)<input type="number" step="0.1" value={form.temperature} disabled={!canWrite} onChange={function (e) { setField("temperature", Number(e.target.value)); }} /></label>
      <label className="login-field">Max tokens (50-4000)<input type="number" value={form.max_tokens} disabled={!canWrite} onChange={function (e) { setField("max_tokens", Number(e.target.value)); }} /></label>
      <label className="login-field">Warm-up question count (1-10)<input type="number" value={form.warmup_question_count} disabled={!canWrite} onChange={function (e) { setField("warmup_question_count", Number(e.target.value)); }} /></label>
      <label className="login-field">Language<input type="text" value={form.language} disabled={!canWrite} onChange={function (e) { setField("language", e.target.value); }} /></label>
      <p className="muted">
        No duration limit, retry policy, or voice settings beyond the above are exposed -- voice_bot.py has
        no code path for them today (English-only, no configurable timeout). Listing them would misrepresent
        what the bot can actually do.
      </p>
      {canWrite && (
        <div className="stage-config-actions">
          <button className="btn" onClick={handleSaveDraft} disabled={save.saving}>{save.saving ? "Saving…" : "Save draft"}</button>
          {canActivate && data.draft && <button className="btn btn-primary" onClick={handleActivate} disabled={save.saving}>Activate draft</button>}
        </div>
      )}
      {save.error && <div className="banner-error-inline">{save.error}</div>}
    </React.Fragment>
  );
}

function CommsTemplatesTab(props) {
  var api = props.api;
  var canManageTemplates = props.canManageTemplates;
  var state = React.useState({ loading: true, error: null, templates: null });
  var data = state[0];
  var setData = state[1];
  var editState = React.useState({});
  var edits = editState[0];
  var setEdits = editState[1];
  var previewState = React.useState({});
  var previews = previewState[0];
  var setPreviews = previewState[1];
  var actionState = React.useState({ busy: false, error: null });
  var action = actionState[0];
  var setAction = actionState[1];

  function load() {
    setData(function (d) { return Object.assign({}, d, { loading: true, error: null }); });
    api.listCommsTemplates()
      .then(function (result) { setData({ loading: false, error: null, templates: result.templates }); })
      .catch(function (err) { setData(function (d) { return Object.assign({}, d, { loading: false, error: err.message }); }); });
  }

  React.useEffect(function () { load(); }, []);

  function handlePreview(templateId) {
    api.previewCommsTemplate(templateId)
      .then(function (result) { setPreviews(function (p) { return Object.assign({}, p, { [templateId]: result }); }); })
      .catch(function (err) { setAction({ busy: false, error: err.message }); });
  }

  function handleCreateDraft(templateId) {
    var e = edits[templateId] || {};
    if (!e.body) return;
    setAction({ busy: true, error: null });
    api.createCommsTemplateDraft(templateId, e.subject || "", e.body)
      .then(function () { setAction({ busy: false, error: null }); setEdits(function (ed) { return Object.assign({}, ed, { [templateId]: undefined }); }); load(); })
      .catch(function (err) { setAction({ busy: false, error: err.message }); });
  }

  function handleActivateVersion(templateId, version) {
    if (!window.confirm("Activate template version " + version + "?")) return;
    setAction({ busy: true, error: null });
    api.activateCommsTemplateVersion(templateId, version)
      .then(function () { setAction({ busy: false, error: null }); load(); })
      .catch(function (err) { setAction({ busy: false, error: err.message }); });
  }

  if (data.error) return <div className="banner banner-error">{data.error}</div>;
  if (data.loading) return <p className="muted">Loading…</p>;

  return (
    <React.Fragment>
      <p className="muted">
        Reusable, versioned templates -- editing and activating one here changes what the real send path
        uses next. Previews always use clearly-labeled SYNTHETIC example data, never a real candidate's
        details.
      </p>
      {action.error && <div className="banner-error-inline">{action.error}</div>}
      {Object.keys(data.templates).map(function (templateId) {
        var versions = data.templates[templateId].slice().sort(function (a, b) { return b.version - a.version; });
        var active = versions.filter(function (v) { return v.status === "active"; })[0];
        var edit = edits[templateId] || { subject: active ? active.subject : "", body: active ? active.body : "" };
        var preview = previews[templateId];
        return (
          <div key={templateId} style={{ marginBottom: 24 }}>
            <h4 className="subsection-heading">{templateId}</h4>
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
                        <td>{canManageTemplates && v.status !== "active" && <button className="btn" onClick={function () { handleActivateVersion(templateId, v.version); }} disabled={action.busy}>Activate</button>}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="stage-config-actions"><button className="btn" onClick={function () { handlePreview(templateId); }}>Preview with example data</button></div>
            {preview && (
              <div className="table-card" style={{ marginTop: 8 }}>
                <p className="muted">Synthetic example data -- never real candidate information.</p>
                {preview.rendered_subject && <p><strong>Subject:</strong> {preview.rendered_subject}</p>}
                <pre style={{ whiteSpace: "pre-wrap" }}>{preview.rendered_body}</pre>
              </div>
            )}
            {canManageTemplates && (
              <React.Fragment>
                <label className="login-field">Subject<input type="text" value={edit.subject} onChange={function (e) { setEdits(function (ed) { return Object.assign({}, ed, { [templateId]: Object.assign({}, edit, { subject: e.target.value }) }); }); }} /></label>
                <label className="login-field">Body<textarea rows={6} value={edit.body} onChange={function (e) { setEdits(function (ed) { return Object.assign({}, ed, { [templateId]: Object.assign({}, edit, { body: e.target.value }) }); }); }} /></label>
                <div className="stage-config-actions"><button className="btn" onClick={function () { handleCreateDraft(templateId); }} disabled={action.busy}>Save as new draft version</button></div>
              </React.Fragment>
            )}
          </div>
        );
      })}
    </React.Fragment>
  );
}

function CommsPoliciesTab(props) {
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
    api.getCommsConfig()
      .then(function (result) { setData({ loading: false, error: null, active: result.active, draft: result.draft }); setForm(result.draft || result.active); })
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

  function handleSaveDraft() {
    setSave({ saving: true, error: null, validation: null });
    api.saveCommsConfigDraft({ email: form.email, whatsapp: form.whatsapp, delivery_policies: form.delivery_policies })
      .then(function () { setSave({ saving: false, error: null, validation: null }); load(); })
      .catch(function (err) { setSave({ saving: false, error: err.message, validation: null }); });
  }

  function handleActivate() {
    if (!window.confirm("Activate this configuration? It will replace what the real notification workflow reads next.")) return;
    setSave({ saving: true, error: null, validation: null });
    api.activateCommsConfig()
      .then(function () { setSave({ saving: false, error: null, validation: null }); load(); })
      .catch(function (err) { setSave({ saving: false, error: err.message, validation: null }); });
  }

  if (data.error) return <div className="banner banner-error">{data.error}</div>;
  if (data.loading || !form) return <p className="muted">Loading…</p>;

  return (
    <React.Fragment>
      <p className="muted">
        Shared policies -- editing creates/updates a DRAFT; nothing changes for the real notification
        workflow until activated. Fallback between channels is deliberately NOT implemented here: this
        console only lets you pick ONE default channel per workflow, since an automatic fallback could
        violate a candidate's consent/preference for a given channel if built without that context.
      </p>
      <label className="review-card-checkbox-label">
        <input type="checkbox" checked={form.email.enabled} disabled={!canWrite} onChange={function (e) { setField("email.enabled", e.target.checked); }} /> {" "}Email enabled
      </label>
      <label className="login-field">Email sender display name<input type="text" value={form.email.sender_display_name} disabled={!canWrite} onChange={function (e) { setField("email.sender_display_name", e.target.value); }} /></label>
      <label className="login-field">Email reply-to<input type="email" value={form.email.reply_to} disabled={!canWrite} onChange={function (e) { setField("email.reply_to", e.target.value); }} /></label>
      <label className="review-card-checkbox-label">
        <input type="checkbox" checked={form.whatsapp.enabled} disabled={!canWrite} onChange={function (e) { setField("whatsapp.enabled", e.target.checked); }} /> {" "}WhatsApp enabled
      </label>
      <label className="login-field">
        Default channel for candidate decision notifications
        <select value={form.delivery_policies.default_channel.candidate_decision_notification} disabled={!canWrite} onChange={function (e) { setField("delivery_policies.default_channel.candidate_decision_notification", e.target.value); }}>
          <option value="email">Email</option>
          <option value="whatsapp">WhatsApp</option>
        </select>
      </label>
      <label className="login-field">
        Duplicate-send protection window (minutes, 1-1440)
        <input type="number" value={form.delivery_policies.duplicate_send_window_minutes} disabled={!canWrite} onChange={function (e) { setField("delivery_policies.duplicate_send_window_minutes", Number(e.target.value)); }} />
      </label>
      {canWrite && (
        <div className="stage-config-actions">
          <button className="btn" onClick={handleSaveDraft} disabled={save.saving}>{save.saving ? "Saving…" : "Save draft"}</button>
          {canActivate && data.draft && <button className="btn btn-primary" onClick={handleActivate} disabled={save.saving}>Activate draft</button>}
        </div>
      )}
      {save.error && <div className="banner-error-inline">{save.error}</div>}
    </React.Fragment>
  );
}

function CommsHistoryTab(props) {
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
    api.getCommsConfigHistory(50)
      .then(function (result) { setData({ loading: false, error: null, history: result.history }); })
      .catch(function (err) { setData(function (d) { return Object.assign({}, d, { loading: false, error: err.message }); }); });
  }

  React.useEffect(function () { load(); }, []);

  function handleRollback(historyId) {
    if (!window.confirm("Roll back to this configuration snapshot? This creates a new DRAFT -- you'll need to review and activate it explicitly.")) return;
    setRollback({ busy: true, error: null });
    api.rollbackCommsConfig(historyId)
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
