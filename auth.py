"""Real authentication + RBAC core -- password hashing, JWT issuance/
verification, the permission catalog, and the default role grants.

Until this module existed, NOTHING in this repo authenticated anyone.
Login.jsx picked a role from a dropdown/hardcoded demo account list,
entirely client-side -- the Azure Functions `auth_level=FUNCTION` key on
most endpoints is a flat, shared, all-or-nothing gate (anyone with the
one function key can call any FUNCTION-level endpoint; there has never
been a per-user or per-role distinction enforced anywhere server-side).
This module is the foundation that fixes that: azure-functions/
api_blueprint.py's require_permission() (see that file) uses this
module's verify_token()/effective_permissions() on every protected
endpoint, instead of trusting a role value the frontend supplies.

PERMISSION CATALOG: reconciled against this repo's actual modules, not
copied from a generic template. Two things the original feature request
mentioned that don't exist here, left out rather than invented:
  - "Global Theme Management" (themes.read/themes.update) -- no such
    feature exists in this app (no runtime-configurable UI theme).
  - "matching.run" -- JD/resume matching happens automatically inside
    ResumeMEtrics.py's pipeline, not via a manual "run matching" API
    call a user triggers. jds.read (viewing the active JD list used for
    matching) is the real, existing surface instead.

resumes.upload exists in the catalog for completeness (reconciling with
the real "Resume Upload and Parsing" module), but POST /applications
stays ANONYMOUS/unauthenticated -- it's the public candidate application
form, unaffected by this change, same as before.
"""

import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from secrets_loader import get_secret  # noqa: E402

# ---------------------------------------------------------------------------
# Password hashing (bcrypt -- single well-reviewed dependency, sane default
# cost factor, no separate native-library setup argon2 would need).
# ---------------------------------------------------------------------------
def hash_password(plain_password: str) -> str:
    import bcrypt

    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    import bcrypt

    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        # Malformed/corrupt hash -- never let a weird stored value raise
        # out of a login attempt; just fail the check.
        return False


# ---------------------------------------------------------------------------
# JWT issuance/verification. Short-lived (8h) so a disabled account or a
# role/permission change can't be exploited via a long-lived stale token --
# require_permission() also re-checks the user's live status/roles on every
# call rather than trusting claims alone, see api_blueprint.py.
# ---------------------------------------------------------------------------
TOKEN_LIFETIME_HOURS = 8


def _jwt_secret() -> str:
    secret = get_secret("JWT_SECRET")
    if not secret:
        raise RuntimeError(
            "Missing required secret(s): JWT_SECRET. Add it to your .env file (see .env.example) or "
            "Key Vault -- a long, random value (e.g. `python -c \"import secrets; print(secrets.token_hex(32))\"`)."
        )
    return secret


def create_token(user: dict) -> str:
    """user must have 'id' and 'username'. Role IDs are deliberately NOT
    embedded as a claim -- effective_permissions() always re-reads the
    user's current roles from the database on each request, so a role
    change takes effect on the user's very next call, not only after
    their token expires and they log in again."""
    import jwt

    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        "sub": user["id"],
        "username": user["username"],
        "iat": now,
        "exp": now + datetime.timedelta(hours=TOKEN_LIFETIME_HOURS),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm="HS256")


def verify_token(token: str) -> dict:
    """Returns the decoded payload ({"sub", "username", "iat", "exp"}) or
    raises jwt.InvalidTokenError (expired, bad signature, malformed,
    etc.) -- callers catch that and return 401, see api_blueprint.py."""
    import jwt

    return jwt.decode(token, _jwt_secret(), algorithms=["HS256"])


# ---------------------------------------------------------------------------
# Permission catalog -- stable id, human label, description, module. The
# module groupings match what the Admin Panel's Roles & Permissions page
# displays them under.
# ---------------------------------------------------------------------------
PERMISSIONS = {
    "users.read": {"label": "View users", "description": "List and view user accounts.", "module": "User Management"},
    "users.create": {"label": "Create users", "description": "Create new user accounts.", "module": "User Management"},
    "users.update": {"label": "Edit users", "description": "Edit user profile fields and reset passwords.", "module": "User Management"},
    "users.disable": {"label": "Enable/disable users", "description": "Activate or deactivate user accounts.", "module": "User Management"},

    "roles.read": {"label": "View roles", "description": "List and view role definitions and their permissions.", "module": "Role & Permission Management"},
    "roles.create": {"label": "Create roles", "description": "Create new custom roles.", "module": "Role & Permission Management"},
    "roles.update": {"label": "Edit role permissions", "description": "Change which permissions a role grants.", "module": "Role & Permission Management"},
    "roles.assign": {"label": "Assign roles to users", "description": "Grant or remove a role from a user account.", "module": "Role & Permission Management"},

    "candidates.read": {"label": "View candidates", "description": "View candidate records, profiles, and scores.", "module": "Candidate Management"},
    "candidates.update": {"label": "Review candidates", "description": "Approve/reject candidates individually.", "module": "Candidate Management"},

    "resumes.upload": {"label": "Submit applications", "description": "Submit a resume/application (the public application form is unauthenticated; this permission exists for catalog completeness).", "module": "Resume Upload and Parsing"},

    "jds.read": {"label": "View job descriptions", "description": "View the active job descriptions used for resume matching.", "module": "Resume-JD Matching"},

    "interviews.read": {"label": "View hiring-stage timelines", "description": "View a candidate's hiring-stage/interview timeline.", "module": "Interview Management"},
    "interviews.manage": {"label": "Manage hiring stages", "description": "Record stage progress and configure the pipeline stage list.", "module": "Interview Management"},

    "documents.verify": {"label": "Verify documents", "description": "Run certificate/marksheet verification against Document Intelligence.", "module": "Document Verification"},

    "analytics.read": {"label": "View analytics", "description": "View executive-summary and observability reports.", "module": "Analytics and Reports"},

    "audit.read": {"label": "View audit log", "description": "View the immutable audit log of recorded actions.", "module": "Audit Logs"},

    "screening.propose": {"label": "Propose bulk decisions", "description": "Propose a bulk accept/reject on a batch of screening-phase candidates.", "module": "Candidate Management"},
    "screening.approve": {"label": "Approve bulk decisions", "description": "Approve or reject a recruiter's bulk-decision proposal -- the only place it actually takes effect.", "module": "Candidate Management"},

    "pages.manage": {"label": "Manage page access", "description": "View the page registry and configure which roles may navigate to which pages.", "module": "Page Access Management"},

    "observability.read": {"label": "View AI observability", "description": "View aggregate AI Observability Center metrics (totals, rates, latency percentiles, cost breakdown).", "module": "AI Observability"},
    "observability.traces.read": {"label": "View detailed AI traces", "description": "View individual trace/span detail, including stage-level metadata -- a stronger permission than observability.read, since trace detail is closer to raw debugging information.", "module": "AI Observability"},

    "azure.read": {"label": "View Azure integrations", "description": "View the Azure service integration inventory and current cached status.", "module": "Azure Service Hub"},
    "azure.config.read": {"label": "View Azure integration configuration", "description": "View an integration's detailed configuration, diagnostics, and health-check history (never secret values).", "module": "Azure Service Hub"},
    "azure.manage": {"label": "Run Azure connection checks", "description": "Trigger a live connection health check against an Azure integration.", "module": "Azure Service Hub"},
    "azure.register": {"label": "Register Azure integrations", "description": "Register a new integration instance, or remove an existing registration (never deletes the underlying Azure resource).", "module": "Azure Service Hub"},
    "azure.config.write": {"label": "Edit Azure integration configuration", "description": "Update an integration instance's non-secret configuration.", "module": "Azure Service Hub"},
    "azure.enable": {"label": "Enable/disable Azure integrations", "description": "Enable or disable CDPI's use of a registered integration.", "module": "Azure Service Hub"},
    "azure.resources.discover": {"label": "Discover Azure resources", "description": "Run real Azure Resource Manager discovery for an integration that supports it.", "module": "Azure Service Hub"},
    "azure.resources.write": {"label": "Provision/update Azure resources", "description": "Reserved for future resource lifecycle operations -- no adapter in this release implements a verified write operation.", "module": "Azure Service Hub"},
    "azure.resources.delete": {"label": "Delete Azure resources", "description": "Reserved for future resource lifecycle operations -- no adapter in this release implements a verified delete operation.", "module": "Azure Service Hub"},
    "secrets.read": {"label": "View secrets inventory", "description": "View which secrets this app reads, whether each currently resolves, and from Key Vault or .env -- never the secret values themselves.", "module": "Secrets Management"},
    "secrets.write": {"label": "Set secret values", "description": "Write a secret's value to the local .env file. The value is never stored anywhere else, never returned by any API, and never shown again after submission.", "module": "Secrets Management"},
    "rag.config.read": {"label": "View RAG configuration", "description": "View the active and draft RAG pipeline configuration (chunking, retrieval, generation) and its change history.", "module": "RAG Configuration"},
    "rag.config.write": {"label": "Edit RAG configuration", "description": "Create or update a draft RAG configuration.", "module": "RAG Configuration"},
    "rag.config.activate": {"label": "Activate RAG configuration", "description": "Promote a validated draft RAG configuration to active, or roll back to a prior version.", "module": "RAG Configuration"},
    "rag.preview.run": {"label": "Run RAG retrieval preview", "description": "Run a real retrieval (and optionally generation) preview against a test query using the draft or active configuration.", "module": "RAG Configuration"},
    "rag.prompts.manage": {"label": "Manage RAG prompt templates", "description": "Create draft prompt template versions and activate/roll back which version is live.", "module": "RAG Configuration"},
    "comms.config.read": {"label": "View communications configuration", "description": "View the active and draft email/WhatsApp/delivery-policy configuration, the interview bot configuration, and their change history.", "module": "Communications Configuration"},
    "comms.config.write": {"label": "Edit communications configuration", "description": "Create or update a draft communications or interview bot configuration.", "module": "Communications Configuration"},
    "comms.config.activate": {"label": "Activate communications configuration", "description": "Promote a validated draft to active, or roll back to a prior version.", "module": "Communications Configuration"},
    "comms.test.send": {"label": "Send test communications", "description": "Run a connection test or send a labeled test message to an explicitly chosen recipient.", "module": "Communications Configuration"},
    "comms.templates.manage": {"label": "Manage message templates", "description": "Create draft template versions and activate/roll back which version is live.", "module": "Communications Configuration"},
    "comms.delivery.read": {"label": "View delivery log", "description": "View real delivery/submission records for sent communications.", "module": "Communications Configuration"},
}

PERMISSION_IDS = list(PERMISSIONS.keys())


# ---------------------------------------------------------------------------
# Default system roles. There is deliberately no separate "Super Admin"
# tier above Admin -- Admin IS the top-level role in this app, with every
# permission in the catalog, including roles.create/roles.update (it can
# redefine what any role grants) and screening.approve (it always
# approves recruiter-proposed bulk accept/reject batches, per this app's
# original screening workflow). None of these are seeded onto any account
# automatically -- see repositories.ensure_default_roles()'s docstring
# for how the very first Admin account actually gets created.
# ---------------------------------------------------------------------------
DEFAULT_ROLES = [
    {
        "role_key": "admin",
        "display_name": "Admin",
        "description": "Full platform access: users, roles and permissions, and every application module -- the top-level role.",
        "is_system": True,
        "permission_ids": list(PERMISSION_IDS),
    },
    {
        "role_key": "recruiter",
        "display_name": "Recruiter",
        "description": "Screens and reviews candidates, manages interview stages, proposes (but cannot approve) bulk decisions.",
        "is_system": True,
        "permission_ids": [
            "candidates.read", "candidates.update",
            "resumes.upload", "jds.read",
            "interviews.read", "interviews.manage",
            "documents.verify", "analytics.read",
            "screening.propose",
            # Added for the Recruiter persona's (P02) required Resume
            # Processing / Communication Delivery reports (CDPI Product
            # Personas doc, section 8.4) -- both reports already existed
            # before this grant, gated by these two permissions.
            "observability.read", "comms.delivery.read",
        ],
    },
    {
        "role_key": "interviewer",
        "display_name": "Interviewer",
        "description": "Views candidates and interview timelines, records stage feedback. (Scoping to only an interviewer's own assigned candidates isn't modeled in this repo's data yet -- see README's known limitations.)",
        "is_system": True,
        "permission_ids": [
            "candidates.read", "interviews.read", "interviews.manage",
        ],
    },
    {
        "role_key": "candidate",
        "display_name": "Candidate",
        "description": "No administrative or recruiter permissions. Candidates use the existing anonymous email-based status check (candidates/by-email/{email}), not this permission system, to see their own application.",
        "is_system": True,
        "permission_ids": [],
    },
    # -----------------------------------------------------------------
    # Persona-to-role mapping (CDPI Product Personas and Reporting
    # Requirements, "Centralized RBAC" section): 8 of the 12 documented
    # personas have no role yet. Every permission below already existed
    # before this mapping -- these roles grant existing permission
    # SUBSETS, matching each persona's described responsibilities;
    # nothing here is a new capability. The other 4 personas already map
    # directly: Candidate -> "candidate", Recruiter -> "recruiter",
    # Interviewer -> "interviewer", Platform Administrator/Super Admin ->
    # "admin" (this app has no separate Super Admin tier -- "admin" IS
    # that persona, full permission set, see the role above).
    # -----------------------------------------------------------------
    {
        "role_key": "hiring_manager",
        "display_name": "Hiring Manager",
        "description": "Reviews shortlists and candidate evidence for assigned vacancies, submits hiring feedback/decisions. Read-focused -- cannot change recruitment workflow, scoring, or screening decisions.",
        "is_system": True,
        "permission_ids": ["candidates.read", "interviews.read", "analytics.read"],
    },
    {
        "role_key": "recruitment_ops_manager",
        "display_name": "Recruitment Operations Manager",
        "description": "Monitors pipeline throughput, recruiter workload, and interview/communication operations across the recruitment team. Broader read access than a Recruiter, plus interview-stage management, but not scoring/AI/Azure configuration.",
        "is_system": True,
        "permission_ids": ["candidates.read", "jds.read", "interviews.read", "interviews.manage", "analytics.read", "audit.read", "comms.delivery.read", "observability.read"],
    },
    {
        "role_key": "hr_director",
        "display_name": "HR Director / Talent Acquisition Leader",
        "description": "Executive view of aggregate recruitment performance only -- no individual candidate records, no document access, no configuration permissions. Matches the persona's 'organization-level aggregated data' access scope.",
        "is_system": True,
        "permission_ids": ["analytics.read"],
    },
    {
        "role_key": "recruitment_administrator",
        "display_name": "Recruitment Administrator",
        "description": "Maintains recruitment-specific operational settings (message templates, interview-stage configuration) -- not platform-wide user/role administration, AI configuration, or Azure infrastructure.",
        "is_system": True,
        "permission_ids": ["interviews.manage", "comms.config.read", "comms.templates.manage"],
    },
    {
        "role_key": "ai_configuration_administrator",
        "display_name": "AI Configuration Administrator",
        "description": "Configures candidate-scoring-adjacent RAG settings and prompt templates -- the full rag.* permission set already built for the RAG Configuration Console. No Azure credential or platform security access.",
        "is_system": True,
        "permission_ids": [
            "rag.config.read", "rag.config.write", "rag.config.activate", "rag.preview.run", "rag.prompts.manage",
            # Added for the persona's required Score Distribution Report
            # (CDPI Product Personas doc section 8.4, P09) -- reuses the
            # existing Admin Reports score-distribution chart, gated the
            # same way it already was for every other role that sees it.
            "analytics.read",
        ],
    },
    {
        "role_key": "azure_cloud_administrator",
        "display_name": "Azure / Cloud Administrator",
        "description": "Configures and validates Azure service integrations -- the full azure.* permission set already built for the Azure Service Hub, plus secrets.read to check secret presence (never values). No candidate-scoring or platform-identity access.",
        "is_system": True,
        "permission_ids": ["azure.read", "azure.config.read", "azure.manage", "azure.register", "azure.config.write", "azure.enable", "azure.resources.discover", "secrets.read", "observability.read"],
    },
    {
        "role_key": "ai_operations_mlops",
        "display_name": "AI Operations / MLOps Engineer",
        "description": "Monitors AI workflow reliability, latency, and cost via the AI Observability Center -- read-only, including trace-level detail. Cannot change scoring/RAG configuration or Azure resources.",
        "is_system": True,
        "permission_ids": ["observability.read", "observability.traces.read"],
    },
    {
        "role_key": "security_compliance_auditor",
        "display_name": "Security, Risk & Compliance Auditor",
        "description": "Read-only access to audit evidence, secret presence (never values), and the user/role configuration being audited. Cannot modify anything it reviews -- matches the persona's explicit read-only requirement.",
        "is_system": True,
        "permission_ids": [
            "audit.read", "secrets.read", "users.read", "roles.read",
            # Added for the persona's required Configuration Change Audit
            # / AI Governance Report (CDPI Product Personas doc section
            # 8.4, P12) -- all three are read-only views into config
            # history the auditor must be able to inspect without being
            # able to CHANGE any of it (azure/rag/comms *.write/*.activate
            # permissions are deliberately NOT granted here).
            "rag.config.read", "azure.config.read", "comms.config.read",
        ],
    },
]


def effective_permissions(role_docs: list) -> set:
    """Union of every permission every given role grants -- the
    documented combination rule for users with multiple roles (see
    README). role_docs is a list of role documents (each with
    'permission_ids')."""
    result = set()
    for role in role_docs:
        result.update(role.get("permission_ids", []))
    return result


# ---------------------------------------------------------------------------
# Page registry -- the centralized route/page catalog Dynamic Page Access
# Management is built on.
#
# THIS APP HAS NO CLIENT-SIDE ROUTER (no React Router, no bundler -- see
# frontend/App.jsx's doc comment). There is no URL per screen, no file-based
# routing, and nothing resembling `/candidates/:candidateId` -- every
# "page" is a tab key switched entirely in React state. That's a real
# architectural fact of this repo, not a gap papered over: the feature
# request's suggested discovery mechanisms (route metadata, a centralized
# route registry, a build-generated route manifest) all presuppose a
# router. The adaptation used here is the closest honest equivalent
# already established in this very module -- a single code-defined
# catalog (PAGES, below) that IS this app's route registry, the same
# pattern PERMISSIONS/DEFAULT_ROLES already use as this app's permission/
# role catalog. Adding a new tab to App.jsx's NAV_ITEMS and a matching
# entry here is this app's "route-registration mechanism" -- the next
# repositories.sync_pages() call (see that function's docstring) is what
# makes it "automatically discovered" without anyone hand-creating a
# database row for it first.
#
# route_type is "page" for every entry -- this app has no nested or
# parameterized routes (no `/candidates/:id` URL; the one candidate-detail
# view that exists, StageTimelineModal, is a modal opened from JS state,
# not a navigable route), so there is nothing to classify as "nested" or
# "parameterized" yet. Documented here rather than invented.
# ---------------------------------------------------------------------------
PAGES = [
    {
        "page_id": "dashboard", "name": "Dashboard", "route": "dashboard",
        "module": "Candidate Management", "route_type": "page", "is_protected": True,
        "required_permission": "candidates.read",
        "description": "Recruiter/Admin dashboard: candidate list, scores, tier distribution.",
    },
    {
        "page_id": "admin_reports", "name": "Admin Reports", "route": "reports",
        "module": "Analytics and Reports", "route_type": "page", "is_protected": True,
        "required_permission": "analytics.read",
        "description": "Exec-style analytics: token/cost usage, candidate tier distribution, human-review approval rate.",
    },
    {
        "page_id": "pipeline", "name": "Applicant Pipeline", "route": "pipeline",
        "module": "Interview Management", "route_type": "page", "is_protected": True,
        "required_permission": "interviews.manage",
        "description": "Kanban-style hiring-stage pipeline board, including the pending bulk-decision approvals queue.",
    },
    {
        "page_id": "review_queue", "name": "Review Queue", "route": "review",
        "module": "Candidate Management", "route_type": "page", "is_protected": True,
        "required_permission": "candidates.update",
        "description": "Human Review Queue: individual approve/reject and bulk-propose accept/reject.",
    },
    {
        "page_id": "recruiter_reports", "name": "Recruiter Reports", "route": "recruiter-reports",
        "module": "Analytics and Reports", "route_type": "page", "is_protected": True,
        "required_permission": "screening.propose",
        "description": "Recruiter-facing reports: stage funnel, performance ratings, this recruiter's bulk-proposal status.",
    },
    {
        "page_id": "user_management", "name": "Users", "route": "users",
        "module": "User Management", "route_type": "page", "is_protected": True,
        "required_permission": "users.read",
        "description": "Admin Panel: create/edit/disable user accounts, assign roles.",
    },
    {
        "page_id": "role_management", "name": "Roles & Permissions", "route": "roles",
        "module": "Role & Permission Management", "route_type": "page", "is_protected": True,
        "required_permission": "roles.read",
        "description": "Admin Panel: the permission catalog grouped by module, and every role's grants.",
    },
    {
        "page_id": "page_access_management", "name": "Page Access", "route": "page-access",
        "module": "Page Access Management", "route_type": "page", "is_protected": True,
        "required_permission": "pages.manage",
        "description": "Admin Panel: this page -- the page registry and the per-role page-access policy editor.",
    },
    {
        "page_id": "ai_observability", "name": "AI Observability", "route": "ai-observability",
        "module": "AI Observability", "route_type": "page", "is_protected": True,
        "required_permission": "observability.read",
        "description": "Admin Panel: LLM call metrics (tokens/cost/latency) and AI pipeline-stage traces "
                        "(embedding generation, document extraction, etc.) -- see observability.py.",
    },
    {
        "page_id": "azure_observability_report", "name": "Azure Observability Report", "route": "azure-observability-report",
        "module": "AI Observability", "route_type": "page", "is_protected": True,
        "required_permission": "observability.read",
        "description": "Admin Panel: real Azure Monitor/Log Analytics telemetry query-back (request volume, latency, "
                        "AI usage, recent errors, ingestion health) composed with the existing AI Observability Center "
                        "and Azure Readiness data into one report -- see azure_monitor_query.py and docs/AZURE_OBSERVABILITY.md.",
    },
    {
        "page_id": "azure_services", "name": "Azure Service Hub", "route": "azure-services",
        "module": "Azure Service Hub", "route_type": "page", "is_protected": True,
        "required_permission": "azure.read",
        "description": "Admin Panel: the registry of Azure integrations this repo actually has code for, "
                        "their configuration status, and live connection checks where one can be safely run "
                        "-- see azure_integrations.py.",
    },
    {
        "page_id": "secrets_management", "name": "Secrets", "route": "secrets",
        "module": "Secrets Management", "route_type": "page", "is_protected": True,
        "required_permission": "secrets.read",
        "description": "Admin Panel: every secret this app actually reads (via secrets_loader.get_secret()), "
                        "whether each currently resolves, and from Key Vault or .env -- never the values "
                        "themselves. See secrets_registry.py.",
    },
    {
        "page_id": "rag_config", "name": "RAG Configuration", "route": "rag-config",
        "module": "RAG Configuration", "route_type": "page", "is_protected": True,
        "required_permission": "rag.config.read",
        "description": "Admin Panel: configure and version the resume-matching RAG pipeline's chunking, "
                        "retrieval, generation, and prompt templates -- see rag_config.py.",
    },
    {
        "page_id": "communications_config", "name": "Communications Configuration", "route": "communications-config",
        "module": "Communications Configuration", "route_type": "page", "is_protected": True,
        "required_permission": "comms.config.read",
        "description": "Admin Panel: configure email/WhatsApp/SMS delivery, the interview bot, message "
                        "templates, and delivery policies -- see comms_config.py.",
    },
    {
        "page_id": "azure_readiness", "name": "Azure Readiness", "route": "azure-readiness",
        "module": "Azure Service Hub", "route_type": "page", "is_protected": True,
        "required_permission": "azure.config.read",
        "description": "Admin Panel: centralized Azure integration readiness -- one composed status per "
                        "service (NOT_CONFIGURED/AUTHENTICATION_REQUIRED/READY/etc.), Azure CLI login status, "
                        "and the ephemeral provision-test-destroy workflow. See azure_readiness.py.",
    },
    {
        "page_id": "persona_catalogue", "name": "Persona Catalogue", "route": "persona-catalogue",
        "module": "Role & Permission Management", "route_type": "page", "is_protected": True,
        "required_permission": "roles.read",
        "description": "Admin Panel: the 12 representative persona configurations (CDPI Product Personas "
                        "doc Section 8) -- each persona's role mapping, allowed pages, and report "
                        "authorization/status, derived live from the real role and page registry, "
                        "never a separate authorization table. See persona_catalogue.py.",
    },
    {
        "page_id": "candidate_portal", "name": "Candidate Portal", "route": "portal",
        "module": "Resume Upload and Parsing", "route_type": "page", "is_protected": True,
        "required_permission": None,
        "description": "Candidate self-service: submit an application, check status by email. "
                        "Gated by the 'candidate' role directly, not an operation permission -- "
                        "see the Candidate role's docstring in DEFAULT_ROLES above.",
    },
]
PAGE_IDS = [p["page_id"] for p in PAGES]


def default_page_access_policies() -> list:
    """Computes the INITIAL page-access policy matrix for PAGES against
    DEFAULT_ROLES, reproducing today's existing implicit (permission-based)
    page gating as an explicit, persisted policy the first time pages are
    synced into the database -- so shipping this feature doesn't silently
    lock anyone out of a page they could already reach before it existed.

    This is a ONE-TIME SEED, used only by repositories.sync_pages() for a
    page that has no persisted policies yet. A page that already has
    policies (because an admin configured it, or it was seeded before) is
    never touched by this function again -- see that function's docstring
    for the full "secure defaults for newly discovered pages" rule: any
    page added to PAGES *after* this initial seed starts with NO policies
    at all (deny-by-default), not this function's output."""
    policies = []
    for page in PAGES:
        for role in DEFAULT_ROLES:
            if page["required_permission"] is None:
                allowed = page["page_id"] == "candidate_portal" and role["role_key"] == "candidate"
            else:
                allowed = page["required_permission"] in role["permission_ids"]
            policies.append({"page_id": page["page_id"], "role_key": role["role_key"], "allowed": allowed})
    return policies


def effective_pages(role_keys: list, policy_docs: list) -> set:
    """The set of page_ids the given roles may navigate to. Deny-by-default:
    a (page_id, role_key) pair with no policy document at all is NOT
    granted -- only an explicit {"allowed": True} policy grants access.
    policy_docs is every PageAccessPolicy document (repositories.
    list_all_page_access_policies()), not pre-filtered by role."""
    allowed = set()
    for policy in policy_docs:
        if policy.get("role_key") in role_keys and policy.get("allowed"):
            allowed.add(policy["page_id"])
    return allowed
