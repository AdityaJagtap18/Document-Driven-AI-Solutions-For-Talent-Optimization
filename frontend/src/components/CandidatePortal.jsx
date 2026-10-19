/**
 * Candidate Self-Service Portal.
 *
 * "Check status" covers the one real read endpoint for candidates
 * (GET /api/candidates/by-email/{email}) in full.
 *
 * "Submit a new application" is now a real, working form — POST
 * /api/applications (azure-functions/api_blueprint.py), which uploads the
 * resume to Blob Storage and lets the existing blob-triggered function
 * (Phase 3) pick it up and run the normal ResumeParser.py pipeline. In
 * mock mode (the default here, see frontend/README.md) this validates
 * input the same way but doesn't actually upload or process anything —
 * switch config.js to mode: "real" against a deployed API to exercise it
 * for real. jd_id/cover_note are collected and sent (visible in the
 * audit log) but NOT acted on by the scoring pipeline yet — see the
 * endpoint's docstring for why; not faked as if they influenced ranking.
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
            {data.result.pipeline_stages && data.result.pipeline_stages.length > 0 && (
              <OwnStageTimeline stages={data.result.pipeline_stages} timeline={data.result.stage_timeline || []} />
            )}
          </div>
        )}
        {data.checked && data.error && (
          <div className="banner banner-error">{data.error}</div>
        )}
      </section>

      <ApplicationForm api={api} />
    </div>
  );
}

var EMPTY_APPLICATION = {
  fullName: "", email: "", phone: "", linkedin: "", jdId: "", coverNote: "",
  resumeFile: null,
};

function ApplicationForm(props) {
  var api = props.api;
  var formState = React.useState(EMPTY_APPLICATION);
  var form = formState[0];
  var setForm = formState[1];
  var jdsState = React.useState([]);
  var jds = jdsState[0];
  var setJds = jdsState[1];
  var submitState = React.useState({ submitting: false, result: null, error: null });
  var submit = submitState[0];
  var setSubmit = submitState[1];

  React.useEffect(function () {
    api.listJds()
      .then(function (r) { setJds(r.jds || []); })
      .catch(function () { setJds([]); }); // non-fatal -- the JD dropdown just stays empty/"Not specified"
  }, []);

  function setField(name) {
    return function (e) {
      var value = e.target.value;
      setForm(function (f) {
        var updated = Object.assign({}, f);
        updated[name] = value;
        return updated;
      });
    };
  }

  function readFileAsBase64(file) {
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onload = function () {
        // reader.result is "data:application/pdf;base64,AAAA..." -- strip the prefix
        var commaIndex = reader.result.indexOf(",");
        resolve(reader.result.slice(commaIndex + 1));
      };
      reader.onerror = function () { reject(new Error("Could not read the selected file.")); };
      reader.readAsDataURL(file);
    });
  }

  function handleSubmit(e) {
    e.preventDefault();
    if (!form.resumeFile) {
      setSubmit({ submitting: false, result: null, error: "Please choose a resume PDF to upload." });
      return;
    }
    setSubmit({ submitting: true, result: null, error: null });

    readFileAsBase64(form.resumeFile)
      .then(function (resumeBase64) {
        return api.submitApplication({
          fullName: form.fullName.trim(),
          email: form.email.trim(),
          phone: form.phone.trim() || null,
          linkedin: form.linkedin.trim() || null,
          jdId: form.jdId || null,
          coverNote: form.coverNote.trim() || null,
          resumeBase64: resumeBase64,
          resumeFilename: form.resumeFile.name,
        });
      })
      .then(function (result) {
        setSubmit({ submitting: false, result: result, error: null });
        setForm(EMPTY_APPLICATION);
      })
      .catch(function (err) {
        setSubmit({ submitting: false, result: null, error: err.message });
      });
  }

  return (
    <section className="portal-section">
      <h3>Submit a new application</h3>
      <p className="muted">
        Fields marked * are required. Everything you enter here is recorded with your submission
        (see frontend/README.md) — resume parsing (name, email, skills, experience, etc.) is done
        fresh from the resume itself, not from what you type below.
      </p>

      <form className="application-form" onSubmit={handleSubmit}>
        <div className="application-form-grid">
          <label className="login-field">
            Full name *
            <input type="text" required value={form.fullName} onChange={setField("fullName")} />
          </label>
          <label className="login-field">
            Email *
            <input type="email" required value={form.email} onChange={setField("email")} />
          </label>
          <label className="login-field">
            Phone
            <input type="tel" value={form.phone} onChange={setField("phone")} />
          </label>
          <label className="login-field">
            LinkedIn URL
            <input type="url" placeholder="https://linkedin.com/in/..." value={form.linkedin} onChange={setField("linkedin")} />
          </label>
          <label className="login-field">
            Applying for
            <select value={form.jdId} onChange={setField("jdId")}>
              <option value="">Not specified</option>
              {jds.map(function (jd) {
                return <option key={jd.jd_id} value={jd.jd_id}>{jd.title || jd.jd_filename}</option>;
              })}
            </select>
          </label>
        </div>

        <label className="login-field">
          Cover note
          <textarea
            rows={4}
            placeholder="Why are you interested in this role?"
            value={form.coverNote}
            onChange={setField("coverNote")}
          />
        </label>

        <label className="login-field">
          Resume (PDF) *
          <input
            type="file"
            accept="application/pdf"
            required
            onChange={function (e) {
              var file = e.target.files && e.target.files[0];
              setForm(function (f) { return Object.assign({}, f, { resumeFile: file || null }); });
            }}
          />
        </label>

        <button className="btn btn-primary" type="submit" disabled={submit.submitting}>
          {submit.submitting ? "Submitting…" : "Submit Application"}
        </button>
      </form>

      {submit.result && (
        <div className="banner banner-success">{submit.result.message || "Application submitted."}</div>
      )}
      {submit.error && (
        <div className="banner banner-error">{submit.error}</div>
      )}
    </section>
  );
}

/** Read-only hiring-stage stepper for the candidate's OWN status check —
 * same stage list/markup StageTimelineModal uses for recruiters, no
 * "record a stage" form here (candidates can't advance their own stage).
 * Rendered inline on this one page, not as a popout, since there's only
 * ever one candidate's timeline to show here. */
function OwnStageTimeline(props) {
  var reachedStages = props.timeline.map(function (t) { return t.stage; });
  var currentStage = props.timeline.length ? props.timeline[props.timeline.length - 1].stage : null;

  return (
    <div className="status-stage-timeline">
      <h4>Hiring process</h4>
      <div className="stepper">
        {props.stages.map(function (stageName) {
          var entry = props.timeline.filter(function (t) { return t.stage === stageName; })[0];
          var reached = reachedStages.indexOf(stageName) !== -1;
          var isCurrent = stageName === currentStage;
          return (
            <div className={"stepper-item" + (reached ? " stepper-item-reached" : "") + (isCurrent ? " stepper-item-current" : "")} key={stageName}>
              <div className="stepper-dot"></div>
              <div className="stepper-content">
                <div className="stepper-label">{stageName}</div>
                {entry && entry.logged_at && (
                  <div className="stepper-meta">{new Date(entry.logged_at).toLocaleDateString()}</div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
