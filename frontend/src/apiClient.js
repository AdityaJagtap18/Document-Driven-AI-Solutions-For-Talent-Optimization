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

// Real, backend-enforced session token (see azure-functions/api_blueprint.py's
// require_permission()) -- set once on login/session-restore, attached to
// every request below. This is the ONLY thing that identifies "who you are"
// to the API; the frontend never sends a role, user id, or permission list
// as if it proved anything -- the backend re-derives all of that from this
// token on every call.
var _authToken = null;

function setAuthToken(token) {
  _authToken = token;
}

async function cdpiRequest(path, options) {
  options = options || {};
  var cfg = window.CDPI_CONFIG;
  var base = cfg.baseUrl.endsWith("/") ? cfg.baseUrl : cfg.baseUrl + "/";
  var url = new URL(path.replace(/^\//, ""), base);
  if (cfg.functionKey && path.indexOf("/health") === -1 && path.indexOf("/by-email/") === -1 && path.indexOf("/applications") === -1) {
    url.searchParams.set("code", cfg.functionKey);
  }

  var headers = Object.assign({ "Content-Type": "application/json" }, options.headers || {});
  if (_authToken) {
    headers.Authorization = "Bearer " + _authToken;
  }

  var response = await fetch(url.toString(), Object.assign(
    {},
    options,
    { headers: headers }
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

/** Same auth/base-URL wiring as cdpiRequest, but for a non-JSON response
 * (CSV export) -- returns the raw text body instead of parsing JSON. */
async function cdpiRequestText(path, options) {
  options = options || {};
  var cfg = window.CDPI_CONFIG;
  var base = cfg.baseUrl.endsWith("/") ? cfg.baseUrl : cfg.baseUrl + "/";
  var url = new URL(path.replace(/^\//, ""), base);
  if (cfg.functionKey) {
    url.searchParams.set("code", cfg.functionKey);
  }
  var headers = Object.assign({}, options.headers || {});
  if (_authToken) {
    headers.Authorization = "Bearer " + _authToken;
  }
  var response = await fetch(url.toString(), Object.assign({}, options, { headers: headers }));
  var text = await response.text();
  if (!response.ok) {
    var error = new Error("Export failed with status " + response.status);
    error.status = response.status;
    throw error;
  }
  return text;
}

window.CDPI_REAL_API = {
  setAuthToken: setAuthToken,
  health: function () {
    return cdpiRequest("/api/health");
  },
  login: function (username, password) {
    return cdpiRequest("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username: username, password: password }),
    });
  },
  getMe: function () {
    return cdpiRequest("/api/auth/me");
  },
  listPermissions: function () {
    return cdpiRequest("/api/manage/permissions");
  },
  listRoles: function () {
    return cdpiRequest("/api/manage/roles");
  },
  createRole: function (role) {
    return cdpiRequest("/api/manage/roles", { method: "POST", body: JSON.stringify(role) });
  },
  updateRole: function (roleKey, fields) {
    return cdpiRequest("/api/manage/roles/" + encodeURIComponent(roleKey), { method: "PUT", body: JSON.stringify(fields) });
  },
  listPages: function (filters) {
    filters = filters || {};
    var qs = Object.keys(filters)
      .filter(function (k) { return filters[k] !== undefined && filters[k] !== ""; })
      .map(function (k) { return encodeURIComponent(k) + "=" + encodeURIComponent(filters[k]); })
      .join("&");
    return cdpiRequest("/api/manage/pages" + (qs ? "?" + qs : ""));
  },
  syncPages: function () {
    return cdpiRequest("/api/manage/pages/sync", { method: "POST" });
  },
  getPageAccess: function (pageId) {
    return cdpiRequest("/api/manage/pages/" + encodeURIComponent(pageId) + "/access");
  },
  setPageAccess: function (pageId, policies) {
    return cdpiRequest("/api/manage/pages/" + encodeURIComponent(pageId) + "/access", {
      method: "PUT", body: JSON.stringify({ policies: policies }),
    });
  },
  listUsers: function () {
    return cdpiRequest("/api/manage/users");
  },
  getUser: function (userId) {
    return cdpiRequest("/api/manage/users/" + encodeURIComponent(userId));
  },
  createUser: function (user) {
    return cdpiRequest("/api/manage/users", { method: "POST", body: JSON.stringify(user) });
  },
  updateUser: function (userId, fields) {
    return cdpiRequest("/api/manage/users/" + encodeURIComponent(userId), { method: "PUT", body: JSON.stringify(fields) });
  },
  setUserStatus: function (userId, status) {
    return cdpiRequest("/api/manage/users/" + encodeURIComponent(userId) + "/status", {
      method: "PUT", body: JSON.stringify({ status: status }),
    });
  },
  setUserRoles: function (userId, roleKeys) {
    return cdpiRequest("/api/manage/users/" + encodeURIComponent(userId) + "/roles", {
      method: "PUT", body: JSON.stringify({ role_keys: roleKeys }),
    });
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
  aiObservabilitySummary: function () {
    return cdpiRequest("/api/manage/ai-observability/summary");
  },
  listAiTraces: function (filters) {
    filters = filters || {};
    var qs = Object.keys(filters)
      .filter(function (k) { return filters[k] !== undefined && filters[k] !== ""; })
      .map(function (k) { return encodeURIComponent(k) + "=" + encodeURIComponent(filters[k]); })
      .join("&");
    return cdpiRequest("/api/manage/ai-observability/traces" + (qs ? "?" + qs : ""));
  },
  getAiTrace: function (traceId) {
    return cdpiRequest("/api/manage/ai-observability/traces/" + encodeURIComponent(traceId));
  },
  listAzureAdapters: function () {
    return cdpiRequest("/api/manage/azure-service-adapters");
  },
  listAzureIntegrations: function () {
    return cdpiRequest("/api/manage/azure-services");
  },
  getAzureIntegration: function (integrationId) {
    return cdpiRequest("/api/manage/azure-services/" + encodeURIComponent(integrationId));
  },
  validateAzureIntegrationConfig: function (adapterType) {
    return cdpiRequest("/api/manage/azure-services/validate", { method: "POST", body: JSON.stringify({ adapter_type: adapterType }) });
  },
  registerAzureIntegration: function (integrationId, adapterType, displayName, environment, config) {
    return cdpiRequest("/api/manage/azure-services", {
      method: "POST",
      body: JSON.stringify({
        integration_id: integrationId, adapter_type: adapterType,
        display_name: displayName, environment: environment, config: config || {},
      }),
    });
  },
  updateAzureIntegrationConfig: function (integrationId, config, expectedVersion) {
    return cdpiRequest("/api/manage/azure-services/" + encodeURIComponent(integrationId) + "/config", {
      method: "PUT", body: JSON.stringify({ config: config, expected_version: expectedVersion }),
    });
  },
  setAzureIntegrationEnabled: function (integrationId, enabled) {
    return cdpiRequest("/api/manage/azure-services/" + encodeURIComponent(integrationId) + "/enabled", {
      method: "PUT", body: JSON.stringify({ enabled: enabled }),
    });
  },
  checkAzureIntegration: function (integrationId) {
    return cdpiRequest("/api/manage/azure-services/" + encodeURIComponent(integrationId) + "/check", { method: "POST" });
  },
  getAzureIntegrationHealthHistory: function (integrationId, limit) {
    return cdpiRequest("/api/manage/azure-services/" + encodeURIComponent(integrationId) + "/health-history" + (limit ? "?limit=" + encodeURIComponent(limit) : ""));
  },
  getAzureIntegrationConfigHistory: function (integrationId, limit) {
    return cdpiRequest("/api/manage/azure-services/" + encodeURIComponent(integrationId) + "/config-history" + (limit ? "?limit=" + encodeURIComponent(limit) : ""));
  },
  rollbackAzureIntegrationConfig: function (integrationId, historyId) {
    return cdpiRequest("/api/manage/azure-services/" + encodeURIComponent(integrationId) + "/config-history/" + encodeURIComponent(historyId) + "/rollback", { method: "POST" });
  },
  discoverAzureResources: function (integrationId) {
    return cdpiRequest("/api/manage/azure-services/" + encodeURIComponent(integrationId) + "/discover-resources", { method: "POST" });
  },
  removeAzureIntegration: function (integrationId) {
    return cdpiRequest("/api/manage/azure-services/" + encodeURIComponent(integrationId), { method: "DELETE" });
  },
  listSecrets: function () {
    return cdpiRequest("/api/manage/secrets");
  },
  setSecret: function (key, value) {
    return cdpiRequest("/api/manage/secrets/" + encodeURIComponent(key), { method: "PUT", body: JSON.stringify({ value: value }) });
  },
  getRagConfig: function () {
    return cdpiRequest("/api/manage/rag-config");
  },
  validateRagConfig: function (config) {
    return cdpiRequest("/api/manage/rag-config/validate", { method: "POST", body: JSON.stringify(config) });
  },
  saveRagConfigDraft: function (config) {
    return cdpiRequest("/api/manage/rag-config/draft", { method: "PUT", body: JSON.stringify(config) });
  },
  activateRagConfig: function (acknowledgeReindexImpact) {
    return cdpiRequest("/api/manage/rag-config/activate", {
      method: "POST", body: JSON.stringify({ acknowledge_reindex_impact: !!acknowledgeReindexImpact }),
    });
  },
  getRagConfigHistory: function (limit) {
    return cdpiRequest("/api/manage/rag-config/history" + (limit ? "?limit=" + encodeURIComponent(limit) : ""));
  },
  rollbackRagConfig: function (historyId) {
    return cdpiRequest("/api/manage/rag-config/rollback/" + encodeURIComponent(historyId), { method: "POST" });
  },
  listRagPrompts: function () {
    return cdpiRequest("/api/manage/rag-config/prompts");
  },
  createRagPromptDraft: function (promptId, template) {
    return cdpiRequest("/api/manage/rag-config/prompts/" + encodeURIComponent(promptId) + "/draft", {
      method: "POST", body: JSON.stringify({ template: template }),
    });
  },
  activateRagPromptVersion: function (promptId, version) {
    return cdpiRequest("/api/manage/rag-config/prompts/" + encodeURIComponent(promptId) + "/versions/" + encodeURIComponent(version) + "/activate", { method: "POST" });
  },
  resumeProcessingReport: function () {
    return cdpiRequest("/api/manage/reports/resume-processing");
  },
  communicationDeliveryReport: function () {
    return cdpiRequest("/api/manage/reports/communication-delivery");
  },
  getPersonaCatalogue: function () {
    return cdpiRequest("/api/manage/personas");
  },
  getAzureReadiness: function () {
    return cdpiRequest("/api/manage/azure-readiness");
  },
  getAzureReadinessCliStatus: function () {
    return cdpiRequest("/api/manage/azure-readiness/cli-status");
  },
  getAzureCostUsage: function () {
    return cdpiRequest("/api/manage/azure-readiness/cost-usage");
  },
  getObservabilityReportSummary: function (hours) {
    var qs = hours ? ("?hours=" + encodeURIComponent(hours)) : "";
    return cdpiRequest("/api/manage/observability-report/summary" + qs);
  },
  getObservabilityReportTelemetry: function (hours, errorLimit) {
    var params = [];
    if (hours) params.push("hours=" + encodeURIComponent(hours));
    if (errorLimit) params.push("error_limit=" + encodeURIComponent(errorLimit));
    var qs = params.length ? ("?" + params.join("&")) : "";
    return cdpiRequest("/api/manage/observability-report/telemetry" + qs);
  },
  exportObservabilityReportCsv: function (hours) {
    var qs = hours ? ("?hours=" + encodeURIComponent(hours)) : "";
    return cdpiRequestText("/api/manage/observability-report/export" + qs);
  },
  validateAzureReadiness: function (integrationId) {
    return cdpiRequest("/api/manage/azure-readiness/" + encodeURIComponent(integrationId) + "/validate", { method: "POST" });
  },
  validateAllAzureReadiness: function () {
    return cdpiRequest("/api/manage/azure-readiness/validate-all", { method: "POST" });
  },
  listEphemeralTests: function () {
    return cdpiRequest("/api/manage/azure-readiness/ephemeral-tests");
  },
  planEphemeralTest: function (resourcePlanId, region) {
    return cdpiRequest("/api/manage/azure-readiness/ephemeral-tests/plan", {
      method: "POST", body: JSON.stringify({ resource_plan_id: resourcePlanId, region: region }),
    });
  },
  approveEphemeralTest: function (runId) {
    return cdpiRequest("/api/manage/azure-readiness/ephemeral-tests/" + encodeURIComponent(runId) + "/approve", { method: "POST" });
  },
  executeEphemeralTest: function (runId) {
    return cdpiRequest("/api/manage/azure-readiness/ephemeral-tests/" + encodeURIComponent(runId) + "/execute", { method: "POST" });
  },
  cleanupEphemeralTest: function (runId) {
    return cdpiRequest("/api/manage/azure-readiness/ephemeral-tests/" + encodeURIComponent(runId) + "/cleanup", { method: "POST" });
  },
  testAzureAiFoundry: function () {
    return cdpiRequest("/api/manage/azure-readiness/azure-ai-foundry/test-request", { method: "POST" });
  },
  testApplicationInsightsTelemetry: function () {
    return cdpiRequest("/api/manage/azure-readiness/application-insights/test-telemetry", { method: "POST" });
  },

  getCommsConfig: function () {
    return cdpiRequest("/api/manage/communications-config");
  },
  validateCommsConfig: function (config) {
    return cdpiRequest("/api/manage/communications-config/validate", { method: "POST", body: JSON.stringify(config) });
  },
  saveCommsConfigDraft: function (config) {
    return cdpiRequest("/api/manage/communications-config/draft", { method: "PUT", body: JSON.stringify(config) });
  },
  activateCommsConfig: function () {
    return cdpiRequest("/api/manage/communications-config/activate", { method: "POST" });
  },
  getCommsConfigHistory: function (limit) {
    return cdpiRequest("/api/manage/communications-config/history" + (limit ? "?limit=" + encodeURIComponent(limit) : ""));
  },
  rollbackCommsConfig: function (historyId) {
    return cdpiRequest("/api/manage/communications-config/rollback/" + encodeURIComponent(historyId), { method: "POST" });
  },
  getInterviewBotConfig: function () {
    return cdpiRequest("/api/manage/communications-config/interview-bot");
  },
  saveInterviewBotConfigDraft: function (config) {
    return cdpiRequest("/api/manage/communications-config/interview-bot/draft", { method: "PUT", body: JSON.stringify(config) });
  },
  activateInterviewBotConfig: function () {
    return cdpiRequest("/api/manage/communications-config/interview-bot/activate", { method: "POST" });
  },
  listCommsTemplates: function () {
    return cdpiRequest("/api/manage/communications-config/templates");
  },
  createCommsTemplateDraft: function (templateId, subject, body) {
    return cdpiRequest("/api/manage/communications-config/templates/" + encodeURIComponent(templateId) + "/draft", {
      method: "POST", body: JSON.stringify({ subject: subject, body: body }),
    });
  },
  activateCommsTemplateVersion: function (templateId, version) {
    return cdpiRequest("/api/manage/communications-config/templates/" + encodeURIComponent(templateId) + "/versions/" + encodeURIComponent(version) + "/activate", { method: "POST" });
  },
  previewCommsTemplate: function (templateId) {
    return cdpiRequest("/api/manage/communications-config/templates/" + encodeURIComponent(templateId) + "/preview");
  },
  testEmailConnectivity: function () {
    return cdpiRequest("/api/manage/communications-config/email/test-connectivity", { method: "POST" });
  },
  sendTestEmail: function (to, subject, body) {
    return cdpiRequest("/api/manage/communications-config/email/test-send", {
      method: "POST", body: JSON.stringify({ to: to, subject: subject, body: body, confirm: true }),
    });
  },
  testWhatsappConnectivity: function () {
    return cdpiRequest("/api/manage/communications-config/whatsapp/test-connectivity", { method: "POST" });
  },
  getCommsDeliveryLog: function (correlationId, limit) {
    return cdpiRequest("/api/manage/communications-config/delivery-log?correlation_id=" + encodeURIComponent(correlationId) + (limit ? "&limit=" + encodeURIComponent(limit) : ""));
  },

  previewRagConfig: function (jdText, options) {
    options = options || {};
    return cdpiRequest("/api/manage/rag-config/preview", {
      method: "POST",
      body: JSON.stringify({
        jd_text: jdText, use: options.use, top_k: options.topK,
        include_generation: !!options.includeGeneration, compare: !!options.compare,
      }),
    });
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
  advanceCandidateStage: function (candidateId, stage, updatedBy, notes, performance) {
    return cdpiRequest("/api/candidates/" + encodeURIComponent(candidateId) + "/stage", {
      method: "POST",
      body: JSON.stringify({ stage: stage, updated_by: updatedBy, notes: notes, performance: performance }),
    });
  },
  proposeBulkDecision: function (decision, candidateIds, proposedBy, notes) {
    return cdpiRequest("/api/bulk-decisions", {
      method: "POST",
      body: JSON.stringify({ decision: decision, candidate_ids: candidateIds, proposed_by: proposedBy, notes: notes }),
    });
  },
  listBulkDecisions: function (status) {
    return cdpiRequest("/api/bulk-decisions" + (status ? "?status=" + encodeURIComponent(status) : ""));
  },
  approveBulkDecision: function (decisionId, reviewedBy) {
    return cdpiRequest("/api/bulk-decisions/" + encodeURIComponent(decisionId) + "/approve", {
      method: "POST",
      body: JSON.stringify({ reviewed_by: reviewedBy }),
    });
  },
  rejectBulkDecision: function (decisionId, reviewedBy, notes) {
    return cdpiRequest("/api/bulk-decisions/" + encodeURIComponent(decisionId) + "/reject", {
      method: "POST",
      body: JSON.stringify({ reviewed_by: reviewedBy, notes: notes }),
    });
  },
};
