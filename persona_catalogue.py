"""The 12-persona representative-configuration catalogue (CDPI Product
Personas and Reporting Requirements doc, Section 8).

This module does NOT implement a separate authorization system -- per the
spec's explicit instruction ("Use the platform's existing permission and
page-access configuration mechanisms where possible"), every fact recorded
here is derived from auth.DEFAULT_ROLES and auth.PAGES, the real RBAC/page
registry already enforced by azure-functions/api_blueprint.py's
require_permission(). This module is a read-only DOCUMENTATION LAYER over
that existing enforcement, not a new gate.

Three honest report states (the spec's own vocabulary, Section 8.4):
  - "available":       the report exists, is wired to real data, and the
                        persona's role holds the permission that gates it.
  - "planned":         the report is specified but no implementation
                        exists yet (not even partial/mocked).
  - "data_dependent":  cannot be implemented reliably because the source
                        data or instrumentation this report needs does not
                        exist anywhere in this repo today (not a UI gap --
                        a real missing-data gap, named below per report).

validate_persona_catalogue() is the mechanism that keeps this file honest:
it fails loudly if a report marked "available" is gated by a permission
the persona's actual role doesn't hold, or if a persona references a role
that doesn't exist in auth.DEFAULT_ROLES.
"""

import auth

# ---------------------------------------------------------------------------
# Report catalogue -- one entry per named report across the full Product
# Personas doc (Sections 1-20) and this Section 8's per-persona "required
# reports" lists. Section 8.4's naming is treated as authoritative where the
# two lists name the same thing differently.
#
# gating_permission is None only for reports that are self-scoped (a
# candidate viewing their own record) rather than permission-gated.
# ---------------------------------------------------------------------------
REPORTS = {
    # --- P01 Candidate ---
    "my_applications": {
        "name": "My Applications", "status": "available", "gating_permission": None,
        "note": "Candidate's own application status via the existing anonymous by-email lookup (GET /candidates/by-email/{email}) -- self-scoped, not RBAC-gated.",
    },
    "profile_document_status": {
        "name": "Profile & Document Status", "status": "available", "gating_permission": None,
        "note": "Same by-email lookup surfaces document verification status -- self-scoped.",
    },
    "interview_schedule_candidate": {
        "name": "My Interview Schedule", "status": "data_dependent",
        "gating_permission": None,
        "note": "No structured interview date/time field exists on a candidate record or stage-history entry -- only stage name and timestamp of the stage CHANGE, not a scheduled future time.",
    },
    "pending_actions": {
        "name": "My Pending Actions", "status": "planned", "gating_permission": None,
        "note": "No candidate-facing 'action required' endpoint exists yet.",
    },
    "communication_history_candidate": {
        "name": "My Communication History", "status": "planned", "gating_permission": None,
        "note": "Delivery records exist in comms_delivery_log, but no candidate-facing (by-email, self-scoped) endpoint exposes them yet.",
    },
    "released_interview_feedback": {
        "name": "Released Interview Feedback", "status": "data_dependent", "gating_permission": None,
        "note": "No 'feedback release' flag/workflow exists -- interview feedback is internal-only today, with no concept of selectively releasing it to the candidate.",
    },

    # --- P02 Recruiter / shared with ops ---
    "recruitment_pipeline_report": {
        "name": "Recruitment Pipeline Report", "status": "available", "gating_permission": "interviews.manage",
        "note": "Pipeline.jsx's kanban board + /audit-log stage-change history.",
    },
    "candidate_screening_ranking_report": {
        "name": "Candidate Screening and Ranking Report", "status": "available", "gating_permission": "candidates.read",
        "note": "AdminReports.jsx tier/score/rank charts.",
    },
    "job_wise_applicant_report": {
        "name": "Job-Wise Applicant Report", "status": "data_dependent", "gating_permission": "jds.read",
        "note": "No jd_id field exists on a candidate record -- confirmed in list_jds_endpoint's own docstring in api_blueprint.py. Cannot attribute applicants to a specific job description.",
    },
    "resume_processing_report": {
        "name": "Resume Processing Report", "status": "available", "gating_permission": "observability.read",
        "note": "GET /admin/reports/resume-processing, real document_extraction trace spans.",
    },
    "interview_schedule_feedback_report": {
        "name": "Interview Schedule and Feedback Report", "status": "available", "gating_permission": "interviews.read",
        "note": "StageTimelineModal + interview transcripts.",
    },
    "communication_delivery_report": {
        "name": "Communication Delivery Report", "status": "available", "gating_permission": "comms.delivery.read",
        "note": "GET /admin/reports/communication-delivery, real comms_delivery_log (WhatsApp only -- email has no delivery logging anywhere in this repo).",
    },
    "candidate_status_followup_report": {
        "name": "Candidate Status and Follow-up Report", "status": "planned", "gating_permission": "candidates.read",
        "note": "Underlying stage/status data exists; no aggregated follow-up report has been built on top of it.",
    },

    # --- P03 Hiring Manager ---
    "shortlist_review_report": {
        "name": "Shortlist Review Report", "status": "planned", "gating_permission": "candidates.read",
        "note": "No 'shortlist' concept (a named, curated subset of candidates) exists -- candidates.read returns the full list, not a shortlist view.",
    },
    "candidate_comparison_report": {
        "name": "Candidate Comparison Report", "status": "planned", "gating_permission": "candidates.read",
        "note": "No side-by-side comparison view has been built.",
    },
    "interview_feedback_report": {
        "name": "Interview Feedback Report", "status": "available", "gating_permission": "interviews.read",
        "note": "Same interview transcript/feedback data as P02's Interview Schedule and Feedback Report.",
    },
    "hiring_decision_report": {
        "name": "Hiring Decision Report", "status": "planned", "gating_permission": "candidates.read",
        "note": "No explicit final hiring-decision field beyond the existing accept/reject tier -- no aggregated decision-outcome report exists.",
    },
    "vacancy_status_aging_report": {
        "name": "Vacancy Status and Aging Report", "status": "data_dependent", "gating_permission": "jds.read",
        "note": "No vacancy/job-opening entity with open/close lifecycle dates exists -- job descriptions have no status or aging field.",
    },

    # --- P04 Interviewer ---
    "interview_schedule": {
        "name": "Interview Schedule", "status": "available", "gating_permission": "interviews.read",
        "note": "Reuses existing interview timeline data -- not scoped to only this interviewer's assignments (no assignment concept exists, see README known limitations).",
    },
    "assigned_candidate_briefs": {
        "name": "Assigned Candidate Briefs", "status": "planned", "gating_permission": "interviews.read",
        "note": "No interviewer-to-candidate assignment concept exists in this repo's data model.",
    },
    "pending_interview_feedback": {
        "name": "Pending Interview Feedback", "status": "planned", "gating_permission": "interviews.manage",
        "note": "No per-interviewer 'feedback owed' tracking exists.",
    },
    "interview_completion_report": {
        "name": "Interview Completion Report", "status": "planned", "gating_permission": "interviews.read",
        "note": "No aggregated completion-rate report exists.",
    },
    "feedback_submission_status": {
        "name": "Feedback Submission Status", "status": "planned", "gating_permission": "interviews.manage",
        "note": "No per-interviewer submission-status tracking exists.",
    },
    "interview_assessment_history": {
        "name": "Interview Assessment History", "status": "available", "gating_permission": "interviews.read",
        "note": "Existing interview transcript history.",
    },

    # --- P05 Recruitment Ops Manager ---
    "recruiter_workload_report": {
        "name": "Recruiter Workload Report", "status": "planned", "gating_permission": "analytics.read",
        "note": "No per-recruiter workload/assignment tracking exists.",
    },
    "recruitment_sla_report": {
        "name": "Recruitment SLA Report", "status": "data_dependent", "gating_permission": "analytics.read",
        "note": "No SLA targets are configured anywhere in this repo -- nothing to measure actual timings against.",
    },
    "bottleneck_aging_report": {
        "name": "Bottleneck / Aging Report", "status": "planned", "gating_permission": "interviews.read",
        "note": "No per-stage aging/duration aggregation has been built.",
    },
    "interview_operations_report": {
        "name": "Interview Operations Report", "status": "planned", "gating_permission": "interviews.manage",
        "note": "No operational interview-throughput report exists.",
    },
    "funnel_conversion_report": {
        "name": "Funnel Conversion Report", "status": "planned", "gating_permission": "analytics.read",
        "note": "Only a partial tier-count proxy exists via the executive summary -- not a full stage-to-stage conversion funnel.",
    },
    "workflow_exception_report": {
        "name": "Workflow Exception Report", "status": "planned", "gating_permission": "audit.read",
        "note": "No exception/anomaly flagging exists in the stage-change pipeline.",
    },

    # --- P06 HR Director ---
    "executive_recruitment_overview": {
        "name": "Executive Recruitment Overview", "status": "available", "gating_permission": "analytics.read",
        "note": "GET /analytics/executive-summary + AdminReports.jsx.",
    },
    "org_level_recruitment_pipeline": {
        "name": "Organization-Level Recruitment Pipeline", "status": "planned", "gating_permission": "analytics.read",
        "note": "No org/department-level rollup exists on top of the existing pipeline data.",
    },
    "time_to_hire_report": {
        "name": "Time-to-Hire Report", "status": "data_dependent", "gating_permission": "analytics.read",
        "note": "No explicit 'hire' event exists to compute a duration against (only stage-change timestamps).",
    },
    "hiring_plan_attainment_report": {
        "name": "Hiring Plan Attainment Report", "status": "data_dependent", "gating_permission": "analytics.read",
        "note": "No hiring plan/target configuration exists anywhere in this repo.",
    },
    "recruiter_productivity_summary": {
        "name": "Recruiter Productivity Summary", "status": "planned", "gating_permission": "analytics.read",
        "note": "Same gap as Recruiter Workload Report -- no per-recruiter attribution exists.",
    },
    "source_effectiveness_report": {
        "name": "Source Effectiveness Report", "status": "data_dependent", "gating_permission": "analytics.read",
        "note": "No candidate-source field exists on a candidate record.",
    },
    "candidate_experience_report": {
        "name": "Candidate Experience Report", "status": "data_dependent", "gating_permission": "analytics.read",
        "note": "No response-time or satisfaction capture exists anywhere.",
    },
    "hiring_quality_retention_report": {
        "name": "Hiring Quality and Retention Report", "status": "data_dependent", "gating_permission": "analytics.read",
        "note": "Flagged in the original Product Personas doc itself (Section 19.5) -- requires post-hire instrumentation this repo doesn't have.",
    },
    "org_hiring_efficiency_report": {
        "name": "Organization Hiring Efficiency Report", "status": "planned", "gating_permission": "analytics.read",
        "note": "No org-level efficiency rollup has been built.",
    },

    # --- P07 Recruitment Administrator ---
    "configuration_inventory": {
        "name": "Configuration Inventory", "status": "available", "gating_permission": "comms.config.read",
        "note": "GET /comms/config (active/draft) + history.",
    },
    "workflow_configuration_report": {
        "name": "Workflow Configuration Report", "status": "available", "gating_permission": "interviews.manage",
        "note": "Existing pipeline-config GET/PUT endpoints (stage list).",
    },
    "template_inventory": {
        "name": "Template Inventory", "status": "available", "gating_permission": "comms.templates.manage",
        "note": "Existing message-template version list/activation history.",
    },
    "configuration_change_history": {
        "name": "Configuration Change History", "status": "available", "gating_permission": "comms.config.read",
        "note": "comms_config_history container.",
    },
    "reference_data_status": {
        "name": "Reference Data Status", "status": "planned", "gating_permission": "jds.read",
        "note": "No reference-data (job titles, locations, etc.) management surface exists.",
    },
    "configuration_completeness_report": {
        "name": "Configuration Completeness Report", "status": "planned", "gating_permission": "comms.config.read",
        "note": "No cross-module 'completeness score' has been built.",
    },

    # --- P08 Platform Administrator ---
    "user_inventory": {
        "name": "User Inventory", "status": "available", "gating_permission": "users.read",
        "note": "Existing Admin Panel user list.",
    },
    "role_permission_matrix": {
        "name": "Role/Permission Matrix", "status": "available", "gating_permission": "roles.read",
        "note": "Existing Roles & Permissions page.",
    },
    "page_access_configuration": {
        "name": "Page Access Configuration", "status": "available", "gating_permission": "pages.manage",
        "note": "Existing Page Access Management page.",
    },
    "privileged_access_report": {
        "name": "Privileged Access Report", "status": "planned", "gating_permission": "roles.read",
        "note": "No aggregated 'who holds high-risk permissions' report exists yet, though the raw role data could support one.",
    },
    "auth_failure_report": {
        "name": "Authentication Failure Report", "status": "available", "gating_permission": "audit.read",
        "note": "audit_log already records login_failed/login_success actions.",
    },
    "platform_configuration_inventory": {
        "name": "Platform Configuration Inventory", "status": "planned", "gating_permission": "roles.read",
        "note": "No single cross-module configuration-inventory report exists (azure/rag/comms each have their own, not unified).",
    },
    "administrative_audit_report": {
        "name": "Administrative Audit Report", "status": "available", "gating_permission": "audit.read",
        "note": "Existing audit log.",
    },
    "configuration_drift_report": {
        "name": "Configuration Drift Report", "status": "data_dependent", "gating_permission": "audit.read",
        "note": "No approved-baseline concept exists to compare current configuration against.",
    },
    "platform_readiness_report": {
        "name": "Platform Readiness Report", "status": "planned", "gating_permission": "azure.config.read",
        "note": "No cross-cutting readiness scorecard has been built.",
    },
    "user_lifecycle_report": {
        "name": "User Lifecycle Report", "status": "planned", "gating_permission": "users.read",
        "note": "Raw audit data (create/disable events) exists but no aggregated lifecycle report has been built.",
    },

    # --- P09 AI Configuration Administrator ---
    "candidate_scoring_configuration_report": {
        "name": "Candidate Scoring Configuration Report", "status": "planned", "gating_permission": "rag.config.read",
        "note": "config/scoring_config.json exists as a static file -- no admin console or API exposes/versions it yet.",
    },
    "score_distribution_report": {
        "name": "Score Distribution Report", "status": "available", "gating_permission": "analytics.read",
        "note": "Reuses the existing Admin Reports score-distribution chart.",
    },
    "scoring_evaluation_report": {
        "name": "Scoring Evaluation Report", "status": "data_dependent", "gating_permission": "rag.config.read",
        "note": "No scoring-evaluation harness (labeled ground truth) exists.",
    },
    "scoring_configuration_comparison": {
        "name": "Scoring Configuration Comparison", "status": "data_dependent", "gating_permission": "rag.config.read",
        "note": "Same gap -- no evaluation harness to compare configurations against.",
    },
    "rag_configuration_report": {
        "name": "RAG Configuration Report", "status": "available", "gating_permission": "rag.config.read",
        "note": "Active/draft RAG config + change history.",
    },
    "retrieval_quality_report": {
        "name": "Retrieval Quality Report", "status": "data_dependent", "gating_permission": "rag.config.read",
        "note": "No labeled relevance dataset exists to score retrieval quality against.",
    },
    "groundedness_citation_report": {
        "name": "Groundedness / Citation Report", "status": "data_dependent", "gating_permission": "rag.config.read",
        "note": "No citation mechanism exists in the generation pipeline.",
    },
    "prompt_version_comparison": {
        "name": "Prompt Version Comparison", "status": "available", "gating_permission": "rag.config.read",
        "note": "Existing prompt-template version history/draft-vs-active compare.",
    },
    "model_embedding_compatibility_report": {
        "name": "Model/Embedding Compatibility Report", "status": "planned", "gating_permission": "rag.config.read",
        "note": "Only a single fixed embedding model is used -- no compatibility-matrix report has been built.",
    },
    "evaluation_dataset_status": {
        "name": "Evaluation Dataset Status", "status": "data_dependent", "gating_permission": "rag.config.read",
        "note": "No evaluation dataset infrastructure exists.",
    },
    "ai_configuration_history": {
        "name": "AI Configuration History", "status": "available", "gating_permission": "rag.config.read",
        "note": "rag_config_history container.",
    },

    # --- P10 Azure / Cloud Administrator ---
    "azure_integration_inventory": {
        "name": "Azure Integration Inventory", "status": "available", "gating_permission": "azure.read",
        "note": "Existing Azure Service Hub integration registry.",
    },
    "azure_configuration_completeness_report": {
        "name": "Azure Configuration Completeness Report", "status": "available", "gating_permission": "azure.config.read",
        "note": "Existing per-integration configuration/diagnostics view.",
    },
    "connection_validation_report": {
        "name": "Connection Validation Report", "status": "available", "gating_permission": "azure.manage",
        "note": "Existing live connection-check history.",
    },
    "key_vault_reference_status": {
        "name": "Key Vault Reference Status", "status": "available", "gating_permission": "secrets.read",
        "note": "Existing Secrets inventory -- presence/source, never values.",
    },
    "cosmos_db_configuration_report": {
        "name": "Cosmos DB Configuration Report", "status": "available", "gating_permission": "azure.config.read",
        "note": "Cosmos integration instance's configuration/diagnostics view.",
    },
    "ai_foundry_deployment_status": {
        "name": "AI Foundry Deployment Status", "status": "data_dependent", "gating_permission": "azure.read",
        "note": "Azure AI Foundry is not integrated anywhere in this repo (confirmed earlier this session) -- nothing to report on.",
    },
    "ai_search_index_status": {
        "name": "AI Search Index Status", "status": "data_dependent", "gating_permission": "azure.read",
        "note": "Azure AI Search is not integrated -- this repo uses a local embedding index (semantic-library), not Azure AI Search.",
    },
    "blob_storage_configuration_report": {
        "name": "Blob Storage Configuration Report", "status": "available", "gating_permission": "azure.config.read",
        "note": "Blob Storage integration instance's configuration/diagnostics view.",
    },
    "azure_functions_health_report": {
        "name": "Azure Functions Health Report", "status": "data_dependent", "gating_permission": "azure.read",
        "note": "No execution-health instrumentation (invocation success rate, cold-start latency) exists on the deployed Function App.",
    },
    "azure_dependency_report": {
        "name": "Azure Dependency Report", "status": "planned", "gating_permission": "azure.read",
        "note": "No cross-integration dependency graph has been built.",
    },
    "environment_comparison": {
        "name": "Environment Comparison", "status": "planned", "gating_permission": "azure.config.read",
        "note": "'environment' is free text per integration instance -- no structured multi-environment comparison exists.",
    },
    "integration_configuration_audit": {
        "name": "Integration Configuration Audit", "status": "available", "gating_permission": "azure.config.read",
        "note": "Existing integration config-change history.",
    },

    # --- P11 AI Operations / MLOps Engineer ---
    "ai_system_health": {
        "name": "AI System Health", "status": "available", "gating_permission": "observability.read",
        "note": "AI Observability Center aggregate metrics.",
    },
    "ai_workflow_traces": {
        "name": "AI Workflow Traces", "status": "available", "gating_permission": "observability.traces.read",
        "note": "Individual trace/span detail.",
    },
    "model_llm_performance": {
        "name": "Model/LLM Performance Report", "status": "available", "gating_permission": "observability.read",
        "note": "Latency percentiles already aggregated in observability.py.",
    },
    "ai_cost_report": {
        "name": "AI Cost Report", "status": "available", "gating_permission": "observability.read",
        "note": "Token/cost breakdown already aggregated in observability.py.",
    },
    "rag_pipeline_health": {
        "name": "RAG Pipeline Health", "status": "available", "gating_permission": "observability.read",
        "note": "Embedding/retrieval stage spans in the same trace log.",
    },
    "evaluation_regression_report": {
        "name": "Evaluation / Regression Report", "status": "data_dependent", "gating_permission": "observability.read",
        "note": "No evaluation harness exists to detect regressions against.",
    },
    "deployment_health": {
        "name": "Deployment Health", "status": "data_dependent", "gating_permission": "observability.read",
        "note": "No deployment-level health signal distinct from overall system health exists.",
    },
    "ai_error_analysis": {
        "name": "AI Error Analysis", "status": "available", "gating_permission": "observability.traces.read",
        "note": "Failed-span detail already in trace log.",
    },
    "token_usage": {
        "name": "Token Usage Report", "status": "available", "gating_permission": "observability.read",
        "note": "Existing token usage aggregation.",
    },
    "embedding_indexing_operations": {
        "name": "Embedding/Indexing Operations Report", "status": "available", "gating_permission": "observability.read",
        "note": "embedding_generation stage spans.",
    },
    "safety_output_validation": {
        "name": "Safety / Output Validation Report", "status": "data_dependent", "gating_permission": "observability.read",
        "note": "No content-safety validation pass exists anywhere in the pipeline.",
    },
    "capacity_limits_report": {
        "name": "Capacity and Limits Report", "status": "data_dependent", "gating_permission": "observability.read",
        "note": "In-memory rate limiters exist in code but are not surfaced as a report.",
    },

    # --- P12 Security / Compliance Auditor ---
    "access_control_audit": {
        "name": "Access Control Audit", "status": "available", "gating_permission": "roles.read",
        "note": "Role/permission matrix cross-referenced with user role assignments (users.read).",
    },
    "privileged_action_audit": {
        "name": "Privileged Action Audit", "status": "available", "gating_permission": "audit.read",
        "note": "Existing audit log.",
    },
    "auth_security_events": {
        "name": "Authentication Security Events", "status": "available", "gating_permission": "audit.read",
        "note": "Same login_failed/login_success events as P08's Authentication Failure Report.",
    },
    "configuration_change_audit": {
        "name": "Configuration Change Audit", "status": "available", "gating_permission": "audit.read",
        "note": "_audit_admin_action-recorded rag/azure/comms *_changed actions.",
    },
    "secret_management_compliance": {
        "name": "Secret Management Compliance Report", "status": "available", "gating_permission": "secrets.read",
        "note": "Existing secrets inventory (presence/source, never values).",
    },
    "candidate_data_access_audit": {
        "name": "Candidate Data Access Audit", "status": "data_dependent", "gating_permission": "audit.read",
        "note": "No field-level 'who viewed this candidate record' logging exists -- the audit log records stage/decision actions, not read access.",
    },
    "data_retention_deletion_report": {
        "name": "Data Retention and Deletion Report", "status": "data_dependent", "gating_permission": "audit.read",
        "note": "No retention-policy tracking exists anywhere.",
    },
    "scoring_governance_report": {
        "name": "Scoring Governance Report", "status": "data_dependent", "gating_permission": "rag.config.read",
        "note": "Same gap as P09's scoring-evaluation reports -- no governance/evaluation harness.",
    },
    "fairness_consistency_evaluation": {
        "name": "Fairness and Consistency Evaluation", "status": "data_dependent", "gating_permission": "rag.config.read",
        "note": "No fairness/bias evaluation harness exists -- confirmed earlier this session.",
    },
    "ai_governance_report": {
        "name": "AI Governance Report", "status": "available", "gating_permission": "rag.config.read",
        "note": "Prompt version/activation/rollback trail.",
    },
    "incident_report": {
        "name": "Incident Report", "status": "data_dependent", "gating_permission": "audit.read",
        "note": "No security-incident tracking exists anywhere in this repo.",
    },
    "compliance_evidence_inventory": {
        "name": "Compliance Evidence Inventory", "status": "planned", "gating_permission": "audit.read",
        "note": "Individual pieces of evidence exist across several reports above, but no single aggregated export has been built.",
    },
}

# ---------------------------------------------------------------------------
# Persona definitions (Section 8.2's table) + the Section 8.5 required
# field set, as far as it is derivable here (data_scope and export
# permission are recorded per persona; allowed/restricted pages are
# computed, not hand-typed, from auth.PAGES + the role's permission_ids --
# see allowed_pages()/restricted_pages() below).
# ---------------------------------------------------------------------------
PERSONAS = [
    {
        "persona_id": "P01", "demo_display_name": "Demo Candidate", "role_key": "candidate",
        "responsibilities": "Submit an application, track its status, view released outcomes for themselves only.",
        "data_scope": "Self only (own application record, looked up by the candidate's own email).",
        "export_permission": False,
        "report_slugs": [
            "my_applications", "profile_document_status", "interview_schedule_candidate",
            "pending_actions", "communication_history_candidate", "released_interview_feedback",
        ],
    },
    {
        "persona_id": "P02", "demo_display_name": "Demo Recruiter", "role_key": "recruiter",
        "responsibilities": "Screen and review candidates, manage interview stages, propose bulk decisions.",
        "data_scope": "All candidates (no per-recruiter candidate assignment exists in this repo's data model).",
        "export_permission": False,
        "report_slugs": [
            "recruitment_pipeline_report", "candidate_screening_ranking_report", "job_wise_applicant_report",
            "resume_processing_report", "interview_schedule_feedback_report", "communication_delivery_report",
            "candidate_status_followup_report",
        ],
    },
    {
        "persona_id": "P03", "demo_display_name": "Demo Hiring Manager", "role_key": "hiring_manager",
        "responsibilities": "Review shortlists and candidate evidence, submit hiring feedback/decisions -- read-focused.",
        "data_scope": "All candidates (no per-vacancy assignment concept exists).",
        "export_permission": False,
        "report_slugs": [
            "job_wise_applicant_report", "shortlist_review_report", "candidate_comparison_report",
            "interview_feedback_report", "hiring_decision_report", "vacancy_status_aging_report",
        ],
    },
    {
        "persona_id": "P04", "demo_display_name": "Demo Interviewer", "role_key": "interviewer",
        "responsibilities": "View candidates and interview timelines, record stage feedback.",
        "data_scope": "All candidates (no per-interviewer assignment scoping exists -- documented limitation, see auth.DEFAULT_ROLES' interviewer role docstring).",
        "export_permission": False,
        "report_slugs": [
            "interview_schedule", "assigned_candidate_briefs", "pending_interview_feedback",
            "interview_completion_report", "feedback_submission_status", "interview_assessment_history",
        ],
    },
    {
        "persona_id": "P05", "demo_display_name": "Demo Recruitment Operations Manager", "role_key": "recruitment_ops_manager",
        "responsibilities": "Monitor pipeline throughput, recruiter workload, interview/communication operations across the team.",
        "data_scope": "All candidates, aggregate operational views.",
        "export_permission": False,
        "report_slugs": [
            "recruitment_pipeline_report", "resume_processing_report", "communication_delivery_report",
            "recruiter_workload_report", "recruitment_sla_report", "bottleneck_aging_report",
            "interview_operations_report", "funnel_conversion_report", "workflow_exception_report",
        ],
    },
    {
        "persona_id": "P06", "demo_display_name": "Demo HR Director", "role_key": "hr_director",
        "responsibilities": "Executive view of aggregate, organization-level recruitment performance only.",
        "data_scope": "Aggregate/organization-level only -- explicitly NO individual candidate records (persona must not receive candidate-data access via its analytics role).",
        "export_permission": False,
        "report_slugs": [
            "executive_recruitment_overview", "org_level_recruitment_pipeline", "time_to_hire_report",
            "hiring_plan_attainment_report", "recruiter_productivity_summary", "source_effectiveness_report",
            "candidate_experience_report", "hiring_quality_retention_report", "org_hiring_efficiency_report",
        ],
    },
    {
        "persona_id": "P07", "demo_display_name": "Demo Recruitment Administrator", "role_key": "recruitment_administrator",
        "responsibilities": "Maintain recruitment-specific operational settings (templates, stage configuration) -- not platform-wide administration.",
        "data_scope": "Configuration objects only -- no candidate records.",
        "export_permission": False,
        "report_slugs": [
            "configuration_inventory", "workflow_configuration_report", "template_inventory",
            "configuration_change_history", "workflow_exception_report", "reference_data_status",
            "configuration_completeness_report",
        ],
    },
    {
        "persona_id": "P08", "demo_display_name": "Demo Platform Administrator", "role_key": "admin",
        "responsibilities": "Full platform access: users, roles/permissions, every module -- this app's single top-level role (no separate Super Admin tier).",
        "data_scope": "Unrestricted (the one persona for which this is appropriate).",
        "export_permission": True,
        "report_slugs": [
            "user_inventory", "role_permission_matrix", "page_access_configuration", "privileged_access_report",
            "auth_failure_report", "platform_configuration_inventory", "administrative_audit_report",
            "configuration_drift_report", "platform_readiness_report", "user_lifecycle_report",
        ],
    },
    {
        "persona_id": "P09", "demo_display_name": "Demo AI Configuration Administrator", "role_key": "ai_configuration_administrator",
        "responsibilities": "Configure candidate-scoring-adjacent RAG settings and prompt templates. No Azure credential or platform security access.",
        "data_scope": "AI/RAG configuration objects only -- no candidate PII, no secret values, no Azure resource access.",
        "export_permission": False,
        "report_slugs": [
            "candidate_scoring_configuration_report", "score_distribution_report", "scoring_evaluation_report",
            "scoring_configuration_comparison", "rag_configuration_report", "retrieval_quality_report",
            "groundedness_citation_report", "prompt_version_comparison", "model_embedding_compatibility_report",
            "evaluation_dataset_status", "ai_configuration_history",
        ],
    },
    {
        "persona_id": "P10", "demo_display_name": "Demo Azure / Cloud Administrator", "role_key": "azure_cloud_administrator",
        "responsibilities": "Configure and validate Azure service integrations. No candidate-scoring or platform-identity access.",
        "data_scope": "Azure integration configuration + secret presence (never secret values, never candidate data).",
        "export_permission": False,
        "report_slugs": [
            "azure_integration_inventory", "azure_configuration_completeness_report", "connection_validation_report",
            "key_vault_reference_status", "cosmos_db_configuration_report", "ai_foundry_deployment_status",
            "ai_search_index_status", "blob_storage_configuration_report", "azure_functions_health_report",
            "azure_dependency_report", "environment_comparison", "integration_configuration_audit",
        ],
    },
    {
        "persona_id": "P11", "demo_display_name": "Demo AI Operations / MLOps Engineer", "role_key": "ai_operations_mlops",
        "responsibilities": "Monitor AI workflow reliability, latency, and cost via the AI Observability Center. Cannot change scoring/RAG configuration or Azure resources.",
        "data_scope": "Aggregate AI telemetry + trace-level detail -- no candidate record access, no configuration-write access.",
        "export_permission": False,
        "report_slugs": [
            "ai_system_health", "ai_workflow_traces", "model_llm_performance", "ai_cost_report",
            "rag_pipeline_health", "evaluation_regression_report", "deployment_health", "ai_error_analysis",
            "token_usage", "embedding_indexing_operations", "safety_output_validation", "capacity_limits_report",
        ],
    },
    {
        "persona_id": "P12", "demo_display_name": "Demo Security, Risk & Compliance Auditor", "role_key": "security_compliance_auditor",
        "responsibilities": "Read-only review of audit evidence, secret presence, user/role configuration, and AI/Azure/comms configuration history. Cannot modify anything it reviews.",
        "data_scope": "Audit/compliance evidence only -- read-only everywhere, no candidate record access, no configuration-write access anywhere.",
        "export_permission": False,
        "report_slugs": [
            "access_control_audit", "privileged_action_audit", "auth_security_events", "configuration_change_audit",
            "secret_management_compliance", "candidate_data_access_audit", "data_retention_deletion_report",
            "scoring_governance_report", "fairness_consistency_evaluation", "ai_governance_report",
            "incident_report", "compliance_evidence_inventory",
        ],
    },
]

PERSONAS_BY_ID = {p["persona_id"]: p for p in PERSONAS}
_ROLES_BY_KEY = {r["role_key"]: r for r in auth.DEFAULT_ROLES}
_PAGES_BY_ID = {p["page_id"]: p for p in auth.PAGES}


def _role_for(persona: dict) -> dict:
    role = _ROLES_BY_KEY.get(persona["role_key"])
    if role is None:
        raise ValueError(f"Persona {persona['persona_id']} references unknown role_key {persona['role_key']!r}")
    return role


def allowed_pages(persona: dict) -> list:
    """Pages this persona's role can reach, computed from the SAME
    permission check auth.default_page_access_policies() uses -- never a
    hand-typed list that could drift from real enforcement."""
    role = _role_for(persona)
    pages = []
    for page in auth.PAGES:
        if page["required_permission"] is None:
            if page["page_id"] == "candidate_portal" and role["role_key"] == "candidate":
                pages.append(page["page_id"])
            continue
        if page["required_permission"] in role["permission_ids"]:
            pages.append(page["page_id"])
    return pages


def restricted_pages(persona: dict) -> list:
    allowed = set(allowed_pages(persona))
    return [p["page_id"] for p in auth.PAGES if p["page_id"] not in allowed]


def report_status(persona: dict, slug: str) -> dict:
    """Returns the report's catalogue entry PLUS whether this persona's
    role is actually authorized for it right now (independent of the
    report's status -- a persona can be denied an 'available' report if
    its role lost the gating permission, which is exactly the drift this
    function exists to catch)."""
    report = REPORTS[slug]
    role = _role_for(persona)
    gate = report["gating_permission"]
    authorized = gate is None or gate in role["permission_ids"]
    return {**report, "slug": slug, "authorized": authorized}

def persona_report_table(persona: dict) -> list:
    return [report_status(persona, slug) for slug in persona["report_slugs"]]


def restricted_report_slugs(persona: dict) -> list:
    """Reports in the full catalogue this persona's role is NOT authorized
    for -- the Section 8.5 'Restricted reports' field. Deliberately
    computed over the WHOLE catalogue, not just this persona's required
    list, so a persona's restriction is visible even for reports it was
    never supposed to need."""
    role = _role_for(persona)
    restricted = []
    for slug, report in REPORTS.items():
        gate = report["gating_permission"]
        if gate is not None and gate not in role["permission_ids"]:
            restricted.append(slug)
    return restricted


def describe_persona(persona: dict) -> dict:
    """The full Section 8.5 required-field record for one persona."""
    role = _role_for(persona)
    reports = persona_report_table(persona)
    return {
        "persona_id": persona["persona_id"],
        "demo_display_name": persona["demo_display_name"],
        "role_key": persona["role_key"],
        "role_display_name": role.get("display_name", role["role_key"]),
        "responsibilities": persona["responsibilities"],
        "allowed_pages": allowed_pages(persona),
        "restricted_pages": restricted_pages(persona),
        "allowed_reports": [r for r in reports if r["authorized"]],
        "restricted_reports": restricted_report_slugs(persona),
        "data_scope": persona["data_scope"],
        "export_permission": persona["export_permission"],
        "report_status_breakdown": {
            "available": sum(1 for r in reports if r["status"] == "available" and r["authorized"]),
            "planned": sum(1 for r in reports if r["status"] == "planned"),
            "data_dependent": sum(1 for r in reports if r["status"] == "data_dependent"),
        },
    }


def describe_all_personas() -> list:
    return [describe_persona(p) for p in PERSONAS]


def validate_persona_catalogue() -> list:
    """Cross-checks every persona against the REAL role/report data this
    module documents, rather than trusting the hand-written tables above.
    Returns a list of problem strings; an empty list means the catalogue
    is internally consistent. Raises nothing -- callers (tests, the API
    endpoint) decide how to react to a non-empty result."""
    problems = []
    seen_role_keys = set()
    for persona in PERSONAS:
        role = _ROLES_BY_KEY.get(persona["role_key"])
        if role is None:
            problems.append(f"{persona['persona_id']}: role_key {persona['role_key']!r} does not exist in auth.DEFAULT_ROLES")
            continue
        seen_role_keys.add(persona["role_key"])
        for slug in persona["report_slugs"]:
            if slug not in REPORTS:
                problems.append(f"{persona['persona_id']}: references unknown report slug {slug!r}")
                continue
            report = REPORTS[slug]
            gate = report["gating_permission"]
            if report["status"] == "available" and gate is not None and gate not in role["permission_ids"]:
                problems.append(
                    f"{persona['persona_id']} ({persona['role_key']}): report {slug!r} is marked 'available' "
                    f"but gated by {gate!r}, which role {persona['role_key']!r} does not hold"
                )
    if len(PERSONAS) != 12:
        problems.append(f"Expected exactly 12 personas, found {len(PERSONAS)}")
    if len(seen_role_keys) != len({p['role_key'] for p in PERSONAS}):
        problems.append("Duplicate role_key mapping detected across personas")
    return problems
