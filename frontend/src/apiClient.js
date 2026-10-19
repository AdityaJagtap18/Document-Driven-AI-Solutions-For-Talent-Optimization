/**
 * Real API client — hits the Azure Functions HTTP API built in
 * azure-functions/api_blueprint.py. Reads its base URL and function key
 * from window.CDPI_CONFIG (see config.js).
 *
 * No bundler here (CDN-only, no Node/npm available in the environment this
 * was built in — see frontend/README.md), so this is a plain classic
 * script, not an ES module: attaches itself to `window.CDPI_REAL_API`
 * instead of using export/import, since those don't share scope across
 * separate <script> tags without a module system.
 *
 * Every function here mirrors one endpoint exactly — same path, same
 * response shape — so swapping mock -> real in App.jsx is a one-line
 * config change, not a rewrite.
 */

async function cdpiRequest(path, options) {
  options = options || {};
  var cfg = window.CDPI_CONFIG;
  var base = cfg.baseUrl.endsWith("/") ? cfg.baseUrl : cfg.baseUrl + "/";
  var url = new URL(path.replace(/^\//, ""), base);
  if (cfg.functionKey && path.indexOf("/health") === -1 && path.indexOf("/by-email/") === -1 && path.indexOf("/applications") === -1) {
    url.searchParams.set("code", cfg.functionKey);
  }

  var response = await fetch(url.toString(), Object.assign(
    {},
    options,
    { headers: Object.assign({ "Content-Type": "application/json" }, options.headers || {}) }
  ));

  var body = await response.json().catch(function () { return {}; });
  if (!response.ok) {
    var error = new Error(body.error || ("Request to " + path + " failed with status " + response.status));
    error.status = response.status;
    error.body = body;
    throw error;
  }
  return body;
}

window.CDPI_REAL_API = {
  health: function () {
    return cdpiRequest("/api/health");
  },
  listCandidates: function (rankingTier) {
    return cdpiRequest("/api/candidates" + (rankingTier ? "?ranking_tier=" + encodeURIComponent(rankingTier) : ""));
  },
  getCandidate: function (candidateId) {
    return cdpiRequest("/api/candidates/" + encodeURIComponent(candidateId));
  },
  getCandidateInterviews: function (candidateId) {
    return cdpiRequest("/api/candidates/" + encodeURIComponent(candidateId) + "/interviews");
  },
  getCandidateByEmail: function (email) {
    return cdpiRequest("/api/candidates/by-email/" + encodeURIComponent(email));
  },
  reviewCandidate: function (candidateId, decision, reviewer, notes) {
    return cdpiRequest("/api/candidates/" + encodeURIComponent(candidateId) + "/review", {
      method: "POST",
      body: JSON.stringify({ decision: decision, reviewer: reviewer, notes: notes }),
    });
  },
  observabilitySummary: function () {
    return cdpiRequest("/api/observability/summary");
  },
  submitApplication: function (fields) {
    return cdpiRequest("/api/applications", {
      method: "POST",
      body: JSON.stringify({
        full_name: fields.fullName, email: fields.email, phone: fields.phone,
        linkedin: fields.linkedin, jd_id: fields.jdId, cover_note: fields.coverNote,
        resume_base64: fields.resumeBase64, resume_filename: fields.resumeFilename,
      }),
    });
  },
  listJds: function () {
    return cdpiRequest("/api/jds");
  },
  listAuditLog: function (action) {
    return cdpiRequest("/api/audit-log" + (action ? "?action=" + encodeURIComponent(action) : ""));
  },
  getPipelineConfig: function () {
    return cdpiRequest("/api/pipeline-config");
  },
  updatePipelineConfig: function (stages) {
    return cdpiRequest("/api/pipeline-config", { method: "PUT", body: JSON.stringify({ stages: stages }) });
  },
  getCandidateTimeline: function (candidateId) {
    return cdpiRequest("/api/candidates/" + encodeURIComponent(candidateId) + "/timeline");
  },
  advanceCandidateStage: function (candidateId, stage, updatedBy, notes) {
    return cdpiRequest("/api/candidates/" + encodeURIComponent(candidateId) + "/stage", {
      method: "POST",
      body: JSON.stringify({ stage: stage, updated_by: updatedBy, notes: notes }),
    });
  },
};
