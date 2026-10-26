/**
 * Job Description Management (manage/jds/*, jd_management.py).
 *
 * Persona: the existing Recruiter (jds.create) creates/uploads/AI-generates/
 * edits/submits JDs; the existing Recruitment Administrator and Admin
 * (jds.approve) approve, reject, publish, and archive them -- the same
 * propose/approve split already established for bulk candidate decisions
 * (screening.propose/screening.approve), not a new pattern invented for
 * this feature. Hiring Manager gets read-only jds.read for context.
 *
 * PUBLISH is the only action that makes a JD live for candidate matching --
 * it syncs into jd_folder/{jd_id}.txt, which the existing, already-
 * verified resume-scoring pipeline (jd_manager.py) reads unchanged. Draft/
 * pending/rejected/archived JDs never reach that file, so they never
 * affect scoring.
 */
var JD_STATUS_BADGE = {
  draft: "badge-neutral", pending_approval: "badge-neutral", approved: "badge-good",
  published: "badge-good", rejected: "badge-bad", archived: "badge-neutral",
};
var JD_STATUS_LABEL = {
  draft: "Draft", pending_approval: "Pending Approval", approved: "Approved",
  published: "Published", rejected: "Rejected", archived: "Archived",
};
var JD_EMPTY_FORM = {
  title: "", description: "", responsibilities: "", required_qualifications: "", preferred_qualifications: "",
  required_skills: "", preferred_skills: "", required_experience: "", seniority_level: "", employment_type: "",
  location: "", work_arrangement: "", department: "", openings_count: "", salary_range: "", hiring_contact: "",
  application_deadline: "", additional_requirements: "",
};

function _jdLinesToList(text) {
  return (text || "").split("\n").map(function (l) { return l.trim(); }).filter(Boolean);
}
function _jdListToLines(list) {
  return (list || []).join("\n");
}

function JDManagement(props) {
  var api = props.api;
  var permissions = props.permissions || [];
  var canCreate = permissions.indexOf("jds.create") !== -1;
  var canApprove = permissions.indexOf("jds.approve") !== -1;

  var tabState = React.useState("board"); // "board" | "create" | "detail"
  var tab = tabState[0];
  var setTab = tabState[1];
  var selectedIdState = React.useState(null);
  var selectedId = selectedIdState[0];
  var setSelectedId = selectedIdState[1];

  function openDetail(jdId) { setSelectedId(jdId); setTab("detail"); }
  function backToBoard() { setSelectedId(null); setTab("board"); }

  return (
    <div className="view" data-testid="jd-management-page">
      <h2>Job Descriptions</h2>
      <p className="muted">
        Create, upload, AI-generate, and manage job descriptions. Publishing a JD is the only action that makes it
        live for candidate matching.
      </p>

      <div className="toolbar">
        <button className={"btn" + (tab === "board" ? " btn-primary" : "")} onClick={backToBoard}>Board</button>
        {canCreate && (
          <button className={"btn" + (tab === "create" ? " btn-primary" : "")} onClick={function () { setTab("create"); }}>
            + New JD
          </button>
        )}
      </div>

      {tab === "board" && <JDBoard api={api} canApprove={canApprove} onOpen={openDetail} />}
      {tab === "create" && <JDCreateForm api={api} onCreated={openDetail} onCancel={backToBoard} />}
      {tab === "detail" && selectedId && (
        <JDDetail api={api} jdId={selectedId} canCreate={canCreate} canApprove={canApprove} onBack={backToBoard} />
      )}
    </div>
  );
}

function JDBoard(props) {
  var api = props.api;
  var statusState = React.useState("");
  var status = statusState[0];
  var setStatus = statusState[1];
  var searchState = React.useState("");
  var search = searchState[0];
  var setSearch = searchState[1];
  var dataState = React.useState({ loading: true, error: null, jds: [] });
  var data = dataState[0];
  var setData = dataState[1];

  function load() {
    setData(function (d) { return Object.assign({}, d, { loading: true, error: null }); });
    api.listJdRecords(status || undefined)
      .then(function (result) { setData({ loading: false, error: null, jds: result.jds || [] }); })
      .catch(function (err) { setData({ loading: false, error: err.message, jds: [] }); });
  }

  React.useEffect(function () { load(); }, [status]);

  var visible = data.jds.filter(function (jd) {
    return !search || (jd.title || "").toLowerCase().indexOf(search.toLowerCase()) !== -1;
  });

  return (
    <div>
      <div className="toolbar">
        <select value={status} onChange={function (e) { setStatus(e.target.value); }}>
          <option value="">All statuses</option>
          {Object.keys(JD_STATUS_LABEL).map(function (s) { return <option key={s} value={s}>{JD_STATUS_LABEL[s]}</option>; })}
        </select>
        <input type="text" placeholder="Search by title…" value={search} onChange={function (e) { setSearch(e.target.value); }} />
        <button className="btn" onClick={load} disabled={data.loading}>{data.loading ? "Loading…" : "Refresh"}</button>
      </div>

      {data.error && <div className="banner banner-error">{data.error}</div>}
      {!data.loading && visible.length === 0 && <p className="muted">No job descriptions found.</p>}

      {visible.length > 0 && (
        <div className="table-card">
          <table className="candidate-table">
            <thead>
              <tr><th>Title</th><th>Status</th><th>Department</th><th>Created by</th><th>Updated</th><th></th></tr>
            </thead>
            <tbody>
              {visible.map(function (jd) {
                return (
                  <tr key={jd.jd_id}>
                    <td>{jd.title}</td>
                    <td><span className={"badge " + (JD_STATUS_BADGE[jd.status] || "badge-neutral")}>{JD_STATUS_LABEL[jd.status] || jd.status}</span></td>
                    <td className="muted">{jd.department || "—"}</td>
                    <td className="muted">{jd.created_by}</td>
                    <td className="muted" style={{ fontSize: "0.82rem" }}>{jd.updated_at || jd.created_at}</td>
                    <td><button className="btn" onClick={function () { props.onOpen(jd.jd_id); }}>View</button></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function JDCreateForm(props) {
  var api = props.api;
  var formState = React.useState(JD_EMPTY_FORM);
  var form = formState[0];
  var setForm = formState[1];
  var savingState = React.useState(false);
  var saving = savingState[0];
  var setSaving = savingState[1];
  var errorState = React.useState(null);
  var error = errorState[0];
  var setError = errorState[1];

  var uploadBusyState = React.useState(false);
  var uploadBusy = uploadBusyState[0];
  var setUploadBusy = uploadBusyState[1];
  var uploadResultState = React.useState(null);
  var uploadResult = uploadResultState[0];
  var setUploadResult = uploadResultState[1];

  var genState = React.useState({ title: "", role_purpose: "", responsibilities: "", required_skills: "", experience: "", seniority: "", department: "" });
  var gen = genState[0];
  var setGen = genState[1];
  var genBusyState = React.useState(false);
  var genBusy = genBusyState[0];
  var setGenBusy = genBusyState[1];
  var genErrorState = React.useState(null);
  var genError = genErrorState[0];
  var setGenError = genErrorState[1];
  var sourceState = React.useState("manual"); // "manual" | "uploaded" | "ai_generated"
  var source = sourceState[0];
  var setSource = sourceState[1];
  var aiMetaState = React.useState(null);
  var aiMeta = aiMetaState[0];
  var setAiMeta = aiMetaState[1];
  var uploadBlobPathState = React.useState(null);
  var uploadBlobPath = uploadBlobPathState[0];
  var setUploadBlobPath = uploadBlobPathState[1];

  function setField(name) {
    return function (e) { var v = e.target.value; setForm(function (f) { var u = Object.assign({}, f); u[name] = v; return u; }); };
  }

  function applyExtractedFields(fields) {
    setForm(function (f) {
      var u = Object.assign({}, f);
      ["title", "description", "required_experience", "seniority_level", "employment_type", "location", "department"].forEach(function (k) {
        if (fields[k]) u[k] = fields[k];
      });
      ["responsibilities", "required_qualifications", "preferred_qualifications", "required_skills", "preferred_skills"].forEach(function (k) {
        if (fields[k]) u[k] = _jdListToLines(fields[k]);
      });
      return u;
    });
  }

  function handleUpload(e) {
    var file = e.target.files && e.target.files[0];
    if (!file) return;
    setUploadBusy(true); setUploadResult(null); setError(null);
    var reader = new FileReader();
    reader.onload = function () {
      var base64 = reader.result.slice(reader.result.indexOf(",") + 1);
      api.uploadJdDocument(base64, file.name)
        .then(function (result) {
          setUploadResult(result);
          setUploadBlobPath(result.source_document_blob_path);
          if (result.extraction_succeeded) { applyExtractedFields(result.extracted_fields); setSource("uploaded"); }
        })
        .catch(function (err) { setError(err.message); })
        .then(function () { setUploadBusy(false); });
    };
    reader.onerror = function () { setUploadBusy(false); setError("Could not read the selected file."); };
    reader.readAsDataURL(file);
  }

  function handleGenerate() {
    if (!gen.title.trim()) { setGenError("Title is required to generate a JD."); return; }
    setGenBusy(true); setGenError(null);
    api.generateJdWithAi({
      title: gen.title, role_purpose: gen.role_purpose,
      responsibilities: _jdLinesToList(gen.responsibilities), required_skills: _jdLinesToList(gen.required_skills),
      experience: gen.experience, seniority: gen.seniority, department: gen.department,
    })
      .then(function (result) {
        applyExtractedFields(result.fields);
        setForm(function (f) { return Object.assign({}, f, { title: result.fields.title || gen.title, seniority_level: gen.seniority, department: gen.department }); });
        setAiMeta(result.metadata);
        setSource("ai_generated");
      })
      .catch(function (err) { setGenError(err.message); })
      .then(function () { setGenBusy(false); });
  }

  function handleSave(andSubmit) {
    setSaving(true); setError(null);
    var payload = {
      title: form.title, description: form.description,
      responsibilities: _jdLinesToList(form.responsibilities),
      required_qualifications: _jdLinesToList(form.required_qualifications),
      preferred_qualifications: _jdLinesToList(form.preferred_qualifications),
      required_skills: _jdLinesToList(form.required_skills),
      preferred_skills: _jdLinesToList(form.preferred_skills),
      required_experience: form.required_experience, seniority_level: form.seniority_level,
      employment_type: form.employment_type, location: form.location, work_arrangement: form.work_arrangement,
      department: form.department, openings_count: form.openings_count || undefined, salary_range: form.salary_range,
      hiring_contact: form.hiring_contact, application_deadline: form.application_deadline,
      additional_requirements: form.additional_requirements, source: source,
    };
    if (uploadBlobPath) payload.source_document_blob_path = uploadBlobPath;
    if (aiMeta) payload.ai_generation_metadata = aiMeta;
    api.createJd(payload)
      .then(function (jd) {
        if (andSubmit) return api.submitJd(jd.jd_id).then(function () { return jd; });
        return jd;
      })
      .then(function (jd) { props.onCreated(jd.jd_id); })
      .catch(function (err) { setError(err.message); })
      .then(function () { setSaving(false); });
  }

  return (
    <div>
      <div className="chart-card" style={{ marginBottom: 16 }}>
        <h4>AI-Assisted Generation (optional)</h4>
        <p className="muted" style={{ fontSize: "0.85rem" }}>Real, billable LLM call -- fills the form below as a draft for you to review and edit.</p>
        <div className="application-form-grid">
          <label className="login-field">Title * <input type="text" value={gen.title} onChange={function (e) { setGen(Object.assign({}, gen, { title: e.target.value })); }} /></label>
          <label className="login-field">Department <input type="text" value={gen.department} onChange={function (e) { setGen(Object.assign({}, gen, { department: e.target.value })); }} /></label>
          <label className="login-field">Seniority <input type="text" value={gen.seniority} onChange={function (e) { setGen(Object.assign({}, gen, { seniority: e.target.value })); }} /></label>
          <label className="login-field">Experience <input type="text" value={gen.experience} onChange={function (e) { setGen(Object.assign({}, gen, { experience: e.target.value })); }} /></label>
        </div>
        <label className="login-field">Role purpose <textarea rows={2} value={gen.role_purpose} onChange={function (e) { setGen(Object.assign({}, gen, { role_purpose: e.target.value })); }} /></label>
        <label className="login-field">Responsibilities (one per line) <textarea rows={3} value={gen.responsibilities} onChange={function (e) { setGen(Object.assign({}, gen, { responsibilities: e.target.value })); }} /></label>
        <label className="login-field">Required skills (one per line) <textarea rows={2} value={gen.required_skills} onChange={function (e) { setGen(Object.assign({}, gen, { required_skills: e.target.value })); }} /></label>
        <button className="btn" onClick={handleGenerate} disabled={genBusy}>{genBusy ? "Generating…" : "Generate with AI"}</button>
        {genError && <div className="banner banner-error" style={{ marginTop: 8 }}>{genError}</div>}
        {aiMeta && <p className="muted" style={{ fontSize: "0.8rem", marginTop: 8 }}>Generated by {aiMeta.provider} / {aiMeta.model} at {new Date(aiMeta.generated_at).toLocaleString()} -- still a draft, edit freely below.</p>}
      </div>

      <div className="chart-card" style={{ marginBottom: 16 }}>
        <h4>Upload an Existing JD (optional)</h4>
        <p className="muted" style={{ fontSize: "0.85rem" }}>PDF only. Extracted fields populate the form below for you to review and edit -- nothing is saved until you submit this form.</p>
        <input type="file" accept="application/pdf" onChange={handleUpload} disabled={uploadBusy} />
        {uploadBusy && <p className="muted">Extracting…</p>}
        {uploadResult && !uploadResult.extraction_succeeded && <div className="banner banner-error" style={{ marginTop: 8 }}>{uploadResult.detail}</div>}
        {uploadResult && uploadResult.extraction_succeeded && <div className="banner banner-success" style={{ marginTop: 8 }}>Extracted fields applied below -- review and edit before saving.</div>}
      </div>

      <div className="chart-card">
        <h4>Job Description</h4>
        <div className="application-form-grid">
          <label className="login-field">Title * <input type="text" required value={form.title} onChange={setField("title")} /></label>
          <label className="login-field">Department <input type="text" value={form.department} onChange={setField("department")} /></label>
          <label className="login-field">Employment type <input type="text" placeholder="Full-time, Contract, ..." value={form.employment_type} onChange={setField("employment_type")} /></label>
          <label className="login-field">Seniority level <input type="text" value={form.seniority_level} onChange={setField("seniority_level")} /></label>
          <label className="login-field">Location <input type="text" value={form.location} onChange={setField("location")} /></label>
          <label className="login-field">Work arrangement
            <select value={form.work_arrangement} onChange={setField("work_arrangement")}>
              <option value="">Not specified</option>
              <option value="onsite">Onsite</option>
              <option value="remote">Remote</option>
              <option value="hybrid">Hybrid</option>
            </select>
          </label>
          <label className="login-field">Openings <input type="number" min="1" value={form.openings_count} onChange={setField("openings_count")} /></label>
          <label className="login-field">Salary range <input type="text" value={form.salary_range} onChange={setField("salary_range")} /></label>
          <label className="login-field">Hiring manager / recruiter <input type="text" value={form.hiring_contact} onChange={setField("hiring_contact")} /></label>
          <label className="login-field">Application deadline <input type="date" value={form.application_deadline} onChange={setField("application_deadline")} /></label>
          <label className="login-field">Required experience <input type="text" value={form.required_experience} onChange={setField("required_experience")} /></label>
        </div>

        <label className="login-field">Description * <textarea rows={3} value={form.description} onChange={setField("description")} /></label>
        <label className="login-field">Responsibilities (one per line) <textarea rows={3} value={form.responsibilities} onChange={setField("responsibilities")} /></label>
        <label className="login-field">Required qualifications (one per line) <textarea rows={2} value={form.required_qualifications} onChange={setField("required_qualifications")} /></label>
        <label className="login-field">Preferred qualifications (one per line) <textarea rows={2} value={form.preferred_qualifications} onChange={setField("preferred_qualifications")} /></label>
        <label className="login-field">Required skills (one per line) <textarea rows={2} value={form.required_skills} onChange={setField("required_skills")} /></label>
        <label className="login-field">Preferred skills (one per line) <textarea rows={2} value={form.preferred_skills} onChange={setField("preferred_skills")} /></label>
        <label className="login-field">Additional requirements <textarea rows={2} value={form.additional_requirements} onChange={setField("additional_requirements")} /></label>

        {error && <div className="banner banner-error">{error}</div>}

        <div className="toolbar">
          <button className="btn" onClick={function () { handleSave(false); }} disabled={saving || !form.title.trim()}>{saving ? "Saving…" : "Save Draft"}</button>
          <button className="btn btn-primary" onClick={function () { handleSave(true); }} disabled={saving || !form.title.trim() || !form.description.trim()}>
            {saving ? "Saving…" : "Save & Submit for Approval"}
          </button>
          <button className="btn" onClick={props.onCancel} disabled={saving}>Cancel</button>
        </div>
      </div>
    </div>
  );
}

function JDDetail(props) {
  var api = props.api;
  var jdId = props.jdId;
  var dataState = React.useState({ loading: true, error: null, jd: null, audit_log: [] });
  var data = dataState[0];
  var setData = dataState[1];
  var busyState = React.useState(false);
  var busy = busyState[0];
  var setBusy = busyState[1];
  var rejectReasonState = React.useState("");
  var rejectReason = rejectReasonState[0];
  var setRejectReason = rejectReasonState[1];
  var showRejectState = React.useState(false);
  var showReject = showRejectState[0];
  var setShowReject = showRejectState[1];
  var actionErrorState = React.useState(null);
  var actionError = actionErrorState[0];
  var setActionError = actionErrorState[1];

  function load() {
    setData(function (d) { return Object.assign({}, d, { loading: true, error: null }); });
    api.getJdRecord(jdId)
      .then(function (result) { setData({ loading: false, error: null, jd: result.jd, audit_log: result.audit_log || [] }); })
      .catch(function (err) { setData({ loading: false, error: err.message, jd: null, audit_log: [] }); });
  }
  React.useEffect(load, [jdId]);

  function runAction(fn) {
    setBusy(true); setActionError(null);
    fn().then(load).catch(function (err) { setActionError(err.message); }).then(function () { setBusy(false); });
  }

  if (data.loading) return <p className="muted">Loading…</p>;
  if (data.error) return (
    <React.Fragment>
      <div className="toolbar"><button className="btn" onClick={props.onBack}>← Back</button></div>
      <div className="banner banner-error">{data.error}</div>
    </React.Fragment>
  );

  var jd = data.jd;
  var isEditable = props.canCreate && ["draft", "rejected"].indexOf(jd.status) !== -1;
  var canSubmit = props.canCreate && ["draft", "rejected"].indexOf(jd.status) !== -1;
  var canApproveReject = props.canApprove && jd.status === "pending_approval";
  var canPublish = props.canApprove && jd.status === "approved";
  var canArchive = props.canApprove && jd.status === "published";

  return (
    <div>
      <div className="toolbar">
        <button className="btn" onClick={props.onBack}>← Back</button>
        {canSubmit && <button className="btn" onClick={function () { runAction(function () { return api.submitJd(jdId); }); }} disabled={busy}>Submit for Approval</button>}
        {canApproveReject && <button className="btn" onClick={function () { runAction(function () { return api.approveJd(jdId); }); }} disabled={busy}>Approve</button>}
        {canApproveReject && <button className="btn" onClick={function () { setShowReject(true); }} disabled={busy}>Reject</button>}
        {canPublish && <button className="btn btn-primary" onClick={function () { runAction(function () { return api.publishJd(jdId); }); }} disabled={busy}>Publish</button>}
        {canArchive && <button className="btn" onClick={function () { runAction(function () { return api.archiveJd(jdId); }); }} disabled={busy}>Archive</button>}
      </div>

      {actionError && <div className="banner banner-error">{actionError}</div>}

      {showReject && (
        <div className="chart-card" style={{ marginBottom: 16 }}>
          <h4>Reject this JD</h4>
          <textarea rows={2} placeholder="Reason (required)" value={rejectReason} onChange={function (e) { setRejectReason(e.target.value); }} />
          <div className="toolbar">
            <button className="btn btn-primary" onClick={function () { runAction(function () { return api.rejectJd(jdId, rejectReason); }); setShowReject(false); setRejectReason(""); }} disabled={!rejectReason.trim()}>Confirm Reject</button>
            <button className="btn" onClick={function () { setShowReject(false); }}>Cancel</button>
          </div>
        </div>
      )}

      <div className="chart-card" style={{ marginBottom: 16 }}>
        <h3 className="subsection-heading">{jd.title}</h3>
        <p>
          <span className={"badge " + (JD_STATUS_BADGE[jd.status] || "badge-neutral")}>{JD_STATUS_LABEL[jd.status] || jd.status}</span>
          {jd.source && <span className="badge badge-neutral" style={{ marginLeft: 8 }}>{jd.source}</span>}
        </p>
        <p className="muted" style={{ fontSize: "0.85rem" }}>
          Created by {jd.created_by} on {new Date(jd.created_at).toLocaleString()}
          {jd.published_at && <React.Fragment> · Published {new Date(jd.published_at).toLocaleString()}</React.Fragment>}
        </p>
        {jd.status === "rejected" && jd.rejection_reason && <div className="banner banner-error">Rejected by {jd.rejected_by}: {jd.rejection_reason}</div>}

        {jd.description && <p>{jd.description}</p>}
        {jd.department && <p><strong>Department:</strong> {jd.department}</p>}
        {jd.location && <p><strong>Location:</strong> {jd.location}{jd.work_arrangement ? " (" + jd.work_arrangement + ")" : ""}</p>}
        {jd.employment_type && <p><strong>Employment type:</strong> {jd.employment_type}</p>}
        {jd.seniority_level && <p><strong>Seniority:</strong> {jd.seniority_level}</p>}
        {jd.required_experience && <p><strong>Required experience:</strong> {jd.required_experience}</p>}
        {jd.salary_range && <p><strong>Salary range:</strong> {jd.salary_range}</p>}
        {jd.hiring_contact && <p><strong>Hiring contact:</strong> {jd.hiring_contact}</p>}
        {jd.application_deadline && <p><strong>Application deadline:</strong> {jd.application_deadline}</p>}

        {jd.responsibilities && jd.responsibilities.length > 0 && (
          <React.Fragment><h4>Responsibilities</h4><ul>{jd.responsibilities.map(function (r, i) { return <li key={i}>{r}</li>; })}</ul></React.Fragment>
        )}
        {jd.required_qualifications && jd.required_qualifications.length > 0 && (
          <React.Fragment><h4>Required Qualifications</h4><ul>{jd.required_qualifications.map(function (r, i) { return <li key={i}>{r}</li>; })}</ul></React.Fragment>
        )}
        {jd.preferred_qualifications && jd.preferred_qualifications.length > 0 && (
          <React.Fragment><h4>Preferred Qualifications</h4><ul>{jd.preferred_qualifications.map(function (r, i) { return <li key={i}>{r}</li>; })}</ul></React.Fragment>
        )}
        {jd.required_skills && jd.required_skills.length > 0 && (
          <React.Fragment><h4>Required Skills</h4><ul>{jd.required_skills.map(function (r, i) { return <li key={i}>{r}</li>; })}</ul></React.Fragment>
        )}
        {jd.preferred_skills && jd.preferred_skills.length > 0 && (
          <React.Fragment><h4>Preferred Skills</h4><ul>{jd.preferred_skills.map(function (r, i) { return <li key={i}>{r}</li>; })}</ul></React.Fragment>
        )}
        {jd.additional_requirements && <React.Fragment><h4>Additional Requirements</h4><p>{jd.additional_requirements}</p></React.Fragment>}

        {!isEditable && jd.status !== "draft" && (
          <p className="muted" style={{ fontSize: "0.8rem", marginTop: 12 }}>This JD can't be edited in its current state ({JD_STATUS_LABEL[jd.status]}).</p>
        )}
      </div>

      <div className="chart-card">
        <h4>Audit Trail</h4>
        {data.audit_log.length === 0 && <p className="muted">No audit events recorded yet.</p>}
        {data.audit_log.length > 0 && (
          <table className="table">
            <thead><tr><th>Time</th><th>Action</th><th>Actor</th></tr></thead>
            <tbody>
              {data.audit_log.map(function (e, i) {
                return <tr key={i}><td>{new Date(e.logged_at).toLocaleString()}</td><td>{e.action}</td><td>{e.actor}</td></tr>;
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
