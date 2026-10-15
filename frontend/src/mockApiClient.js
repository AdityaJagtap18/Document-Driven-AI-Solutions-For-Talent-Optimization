/**
 * Mock API client — same function signatures and response shapes as
 * apiClient.js (window.CDPI_REAL_API), backed by in-memory fake data
 * instead of a real network call. This is what the app uses by default
 * (window.CDPI_CONFIG.mode === "mock" in config.js), since no Azure
 * Functions host is deployed or runnable in this environment yet.
 *
 * Field names deliberately match context-lake/schema/candidate.schema.json
 * and what ResumeParser.py's merge prompt actually produces (see
 * prompts/resume_merge_structured_extraction.v2.md) — not invented shapes.
 */

var MOCK_CANDIDATES = [
  {
    id: "c1", candidate_id: "c1", resume_name: "Priya_Sharma_resume.pdf",
    Name: "Priya Sharma", Email: "priya.sharma@example.com", Phone: "9876543210",
    "JD Match": "88%", "Candidate Type": "Mid-Level", "Resume Strength Score": 8.5,
    "Profile Summary": "Machine Learning engineer with 4 years building production NLP pipelines.",
    ranking_tier: "Recommended for Fast-Track", normalized_weighted_score: 812.4,
    ingested_at: "2026-10-10T09:15:00Z", "Employment Gaps Detected": false,
  },
  {
    id: "c2", candidate_id: "c2", resume_name: "Arjun_Mehta_resume.pdf",
    Name: "Arjun Mehta", Email: "arjun.mehta@example.com", Phone: "9988776655",
    "JD Match": "74%", "Candidate Type": "Junior", "Resume Strength Score": 6.8,
    "Profile Summary": "Recent graduate with strong Python fundamentals and two ML internships.",
    ranking_tier: "To be Considered", normalized_weighted_score: 540.1,
    ingested_at: "2026-10-11T14:02:00Z", "Employment Gaps Detected": false,
  },
  {
    id: "c3", candidate_id: "c3", resume_name: "Fatima_Khan_resume.pdf",
    Name: "Fatima Khan", Email: "fatima.khan@example.com", Phone: "9123456780",
    "JD Match": "91%", "Candidate Type": "Senior", "Resume Strength Score": 9.1,
    "Profile Summary": "Senior ML engineer, 8 years, led model deployment at scale for a fintech platform.",
    ranking_tier: "Recommended for Fast-Track", normalized_weighted_score: 895.7,
    ingested_at: "2026-10-09T11:40:00Z", "Employment Gaps Detected": true,
  },
  {
    id: "c4", candidate_id: "c4", resume_name: "Rohan_Verma_resume.pdf",
    Name: "Rohan Verma", Email: "rohan.verma@example.com", Phone: "9001122334",
    "JD Match": "52%", "Candidate Type": "Junior", "Resume Strength Score": 4.9,
    "Profile Summary": "Backend developer exploring a transition into ML, limited hands-on model experience.",
    ranking_tier: "Rejected", normalized_weighted_score: 301.2,
    ingested_at: "2026-10-12T08:20:00Z", "Employment Gaps Detected": false,
  },
];

var MOCK_INTERVIEWS = {
  c1: [
    { transcript_id: "t1", candidate_id: "c1", interview_type: "pre_generated_questions",
      questions: ["Tell me about a production NLP pipeline you built.", "How do you handle model drift?"] },
  ],
  c3: [
    { transcript_id: "t2", candidate_id: "c3", interview_type: "voicebot_warmup",
      qa_pairs: [{ question: "What drew you to ML engineering?", answer: "I enjoy the full lifecycle from data to deployment." }],
      overall_score: 8 },
  ],
};

var MOCK_AUDIT_LOG = [];

function mockDelay(value) {
  return new Promise(function (resolve) {
    setTimeout(function () { resolve(value); }, 150 + Math.random() * 200);
  });
}

window.CDPI_MOCK_API = {
  health: function () {
    return mockDelay({ status: "ok" });
  },

  listCandidates: function (rankingTier) {
    var filtered = rankingTier
      ? MOCK_CANDIDATES.filter(function (c) { return c.ranking_tier === rankingTier; })
      : MOCK_CANDIDATES;
    return mockDelay({ count: filtered.length, candidates: filtered });
  },

  getCandidate: function (candidateId) {
    var found = MOCK_CANDIDATES.filter(function (c) { return c.candidate_id === candidateId; })[0];
    if (!found) {
      var err = new Error("Candidate '" + candidateId + "' not found.");
      err.status = 404;
      return Promise.reject(err);
    }
    return mockDelay(found);
  },

  getCandidateInterviews: function (candidateId) {
    var interviews = MOCK_INTERVIEWS[candidateId] || [];
    return mockDelay({ candidate_id: candidateId, count: interviews.length, interviews: interviews });
  },

  getCandidateByEmail: function (email) {
    var found = MOCK_CANDIDATES.filter(function (c) { return c.Email === email; })[0];
    if (!found) {
      var err = new Error("No application found for '" + email + "'.");
      err.status = 404;
      return Promise.reject(err);
    }
    return mockDelay({
      Name: found.Name, resume_name: found.resume_name,
      ranking_tier: found.ranking_tier, ingested_at: found.ingested_at,
    });
  },

  reviewCandidate: function (candidateId, decision, reviewer, notes) {
    var found = MOCK_CANDIDATES.filter(function (c) { return c.candidate_id === candidateId; })[0];
    if (!found) {
      var err = new Error("Candidate '" + candidateId + "' not found.");
      err.status = 404;
      return Promise.reject(err);
    }
    found.ranking_tier = decision === "approve" ? "Recommended for Fast-Track" : "Rejected";
    MOCK_AUDIT_LOG.push({ candidate_id: candidateId, action: "human_review_" + decision, reviewer: reviewer, notes: notes });
    return mockDelay({ candidate_id: candidateId, decision: decision, status: "recorded" });
  },

  observabilitySummary: function () {
    return mockDelay({
      total_calls: 23, total_tokens: 15420, total_cost_usd: 0.028114,
      by_label: {
        resume_chunk_summarization: { calls: 12, tokens: 7230, cost_usd: 0.011568 },
        resume_merge_structured_extraction: { calls: 4, tokens: 5890, cost_usd: 0.009547 },
        interview_questions_generation: { calls: 4, tokens: 1680, cost_usd: 0.002688 },
        voicebot_transcript_scoring: { calls: 3, tokens: 620, cost_usd: 0.004311 },
      },
    });
  },
};
