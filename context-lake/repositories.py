"""CRUD functions for the context lake's four Cosmos DB containers.

Flat import on purpose (not a dotted package — the folder name has a hyphen,
same convention voicebot/ already uses for its own local imports). Scripts
that use this module should run with this directory importable, e.g.
`sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))` before
`import repositories`, as migrate_from_csv.py does.
"""

import sys
import os
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from client import get_container  # noqa: E402


def _now():
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# candidates
# ---------------------------------------------------------------------------
def upsert_candidate(candidate: dict) -> dict:
    """candidate must include candidate_id and resume_name. id/ingested_at/
    source_blob_path are filled in if missing. See schema/candidate.schema.json."""
    for field in ("candidate_id", "resume_name"):
        if field not in candidate:
            raise ValueError(f"candidate dict missing required field '{field}'")
    candidate.setdefault("id", candidate["candidate_id"])
    candidate.setdefault("ingested_at", _now())
    candidate.setdefault("source_blob_path", "")
    return get_container("candidates").upsert_item(candidate)


def get_candidate(candidate_id: str):
    """Returns None only when the candidate genuinely doesn't exist.
    Configuration/connection errors (Cosmos DB not provisioned, bad
    credentials, etc.) propagate instead of being silently swallowed into a
    None — callers (e.g. the API layer in azure-functions/api_blueprint.py)
    need to tell "not found" apart from "the backend is unavailable" to
    return the right HTTP status (404 vs. 503)."""
    from azure.cosmos.exceptions import CosmosResourceNotFoundError

    try:
        return get_container("candidates").read_item(item=candidate_id, partition_key=candidate_id)
    except CosmosResourceNotFoundError:
        return None


def find_candidate_by_resume_name(resume_name: str):
    container = get_container("candidates")
    items = list(
        container.query_items(
            query="SELECT * FROM c WHERE c.resume_name = @resume_name",
            parameters=[{"name": "@resume_name", "value": resume_name}],
            enable_cross_partition_query=True,
        )
    )
    return items[0] if items else None


def find_candidate_by_email(email: str):
    """For the candidate self-service portal's status check — candidates
    don't know their internal candidate_id, only the email they applied with."""
    container = get_container("candidates")
    items = list(
        container.query_items(
            query="SELECT * FROM c WHERE c.Email = @email",
            parameters=[{"name": "@email", "value": email}],
            enable_cross_partition_query=True,
        )
    )
    return items[0] if items else None


def list_candidates(ranking_tier: str = None) -> list:
    container = get_container("candidates")
    if ranking_tier:
        query = "SELECT * FROM c WHERE c.ranking_tier = @tier"
        params = [{"name": "@tier", "value": ranking_tier}]
    else:
        query = "SELECT * FROM c"
        params = []
    return list(container.query_items(query=query, parameters=params, enable_cross_partition_query=True))


# ---------------------------------------------------------------------------
# interview_transcripts
# ---------------------------------------------------------------------------
def upsert_interview_transcript(transcript: dict) -> dict:
    """transcript must include transcript_id, candidate_id, interview_type.
    See schema/interview_transcript.schema.json."""
    for field in ("transcript_id", "candidate_id", "interview_type"):
        if field not in transcript:
            raise ValueError(f"transcript dict missing required field '{field}'")
    transcript.setdefault("id", transcript["transcript_id"])
    transcript.setdefault("created_at", _now())
    return get_container("interview_transcripts").upsert_item(transcript)


def list_interview_transcripts(candidate_id: str) -> list:
    container = get_container("interview_transcripts")
    return list(
        container.query_items(
            query="SELECT * FROM c WHERE c.candidate_id = @cid",
            parameters=[{"name": "@cid", "value": candidate_id}],
            partition_key=candidate_id,
        )
    )


# ---------------------------------------------------------------------------
# scoring_history (append-only — one record per scoring run, never overwritten)
# ---------------------------------------------------------------------------
def append_scoring_history(record: dict) -> dict:
    if "candidate_id" not in record:
        raise ValueError("scoring history record missing required field 'candidate_id'")
    record.setdefault("record_id", str(uuid.uuid4()))
    record.setdefault("id", record["record_id"])
    record.setdefault("scored_at", _now())
    return get_container("scoring_history").upsert_item(record)


def list_scoring_history(candidate_id: str) -> list:
    container = get_container("scoring_history")
    return list(
        container.query_items(
            query="SELECT * FROM c WHERE c.candidate_id = @cid ORDER BY c.scored_at ASC",
            parameters=[{"name": "@cid", "value": candidate_id}],
            partition_key=candidate_id,
        )
    )


# ---------------------------------------------------------------------------
# audit_log (append-only, immutable — every AI decision, never overwritten)
# ---------------------------------------------------------------------------
def append_audit_log(entry: dict) -> dict:
    for field in ("candidate_id", "action"):
        if field not in entry:
            raise ValueError(f"audit log entry missing required field '{field}'")
    entry.setdefault("entry_id", str(uuid.uuid4()))
    entry.setdefault("id", entry["entry_id"])
    entry.setdefault("logged_at", _now())
    return get_container("audit_log").upsert_item(entry)


def list_audit_log(candidate_id: str) -> list:
    container = get_container("audit_log")
    return list(
        container.query_items(
            query="SELECT * FROM c WHERE c.candidate_id = @cid ORDER BY c.logged_at ASC",
            parameters=[{"name": "@cid", "value": candidate_id}],
            partition_key=candidate_id,
        )
    )


def list_all_audit_log(action: str = None) -> list:
    """Cross-partition audit_log read, unlike list_audit_log() above which
    is scoped to one candidate. Needed for exec/aggregate reporting (e.g.
    "what's the overall human-review approval rate") -- added for Phase 6's
    executive-summary endpoint, same query-shape as list_candidates()'s
    optional filter above."""
    container = get_container("audit_log")
    if action:
        query = "SELECT * FROM c WHERE c.action = @action"
        params = [{"name": "@action", "value": action}]
    else:
        query = "SELECT * FROM c"
        params = []
    return list(container.query_items(query=query, parameters=params, enable_cross_partition_query=True))


# ---------------------------------------------------------------------------
# pipeline_config (single document: the recruiter-configurable ordered list
# of hiring-process stage names candidates move through, e.g. Shortlisted ->
# Aptitude Exam -> Technical Exam -> ... -> Selected/Rejected). A candidate's
# CURRENT stage and history live in audit_log as action="stage_update"
# entries (see api_blueprint.py's advance_candidate_stage_endpoint) rather
# than a new container -- it's just another immutable event, same as
# human_review_* already is.
# ---------------------------------------------------------------------------
DEFAULT_PIPELINE_STAGES = [
    "Shortlisted", "Aptitude Exam", "Technical Exam",
    "Online Interview", "Offline Interview", "HR Round",
    "Selected", "Rejected",
]


def get_pipeline_config() -> list:
    """Returns the configured ordered stage list, or DEFAULT_PIPELINE_STAGES
    if nobody's configured one yet (first run) -- never raises for "not
    configured", only for a genuine backend problem."""
    from azure.cosmos.exceptions import CosmosResourceNotFoundError

    container = get_container("pipeline_config")
    try:
        doc = container.read_item(item="default", partition_key="default")
        return doc.get("stages", DEFAULT_PIPELINE_STAGES)
    except CosmosResourceNotFoundError:
        return list(DEFAULT_PIPELINE_STAGES)


def set_pipeline_config(stages: list) -> dict:
    """Recruiter sets the ordered stage list. Whole-list replace, not a
    merge -- same semantics as upsert_candidate(); the caller sends the
    full ordered list it wants, not a diff."""
    if not stages or not isinstance(stages, list):
        raise ValueError("stages must be a non-empty list")
    doc = {"id": "default", "stages": stages, "updated_at": _now()}
    return get_container("pipeline_config").upsert_item(doc)


# ---------------------------------------------------------------------------
# bulk_decisions (recruiter proposes a bulk accept/reject on a batch of
# candidates from the screening phase; an admin must approve it before any
# candidate is actually touched -- the AI/scoring pipeline only ever
# produces a RECOMMENDATION (ranking_tier), it never finalizes an outcome,
# and neither does a recruiter acting alone in bulk; only an explicit admin
# approval (api_blueprint.py's approve_bulk_decision_endpoint) applies the
# change to real candidate records. A single-candidate decision via the
# Human Review Queue (review_candidate_endpoint) is unaffected by any of
# this -- that's already a recruiter acting individually on one candidate,
# not an AI/bulk action, so it doesn't need this extra gate.)
# ---------------------------------------------------------------------------
def create_bulk_decision(decision: str, candidate_ids: list, proposed_by: str, notes: str = "") -> dict:
    doc = {
        "id": str(uuid.uuid4()),
        "decision": decision,
        "candidate_ids": candidate_ids,
        "proposed_by": proposed_by,
        "notes": notes,
        "status": "pending",
        "proposed_at": _now(),
        "reviewed_by": None,
        "reviewed_at": None,
    }
    return get_container("bulk_decisions").upsert_item(doc)


def get_bulk_decision(decision_id: str):
    from azure.cosmos.exceptions import CosmosResourceNotFoundError

    try:
        return get_container("bulk_decisions").read_item(item=decision_id, partition_key=decision_id)
    except CosmosResourceNotFoundError:
        return None


def list_bulk_decisions(status: str = None) -> list:
    container = get_container("bulk_decisions")
    if status:
        query = "SELECT * FROM c WHERE c.status = @status ORDER BY c.proposed_at DESC"
        params = [{"name": "@status", "value": status}]
    else:
        query = "SELECT * FROM c ORDER BY c.proposed_at DESC"
        params = []
    return list(container.query_items(query=query, parameters=params, enable_cross_partition_query=True))


def update_bulk_decision(doc: dict) -> dict:
    """Caller passes the full document back (read via get_bulk_decision(),
    mutate, then save) -- same upsert-the-whole-thing convention as
    upsert_candidate()."""
    return get_container("bulk_decisions").upsert_item(doc)


def mark_bulk_decision_reviewed(decision_id: str, status: str, reviewed_by: str, extra: dict = None) -> dict:
    """Read-modify-write helper so callers (api_blueprint.py) don't need
    their own timestamp logic -- sets status/reviewed_by/reviewed_at and
    merges in any extra fields (e.g. {"results": [...]} after applying an
    approval, or {"review_notes": "..."} after a rejection). Raises
    ValueError if the decision doesn't exist or isn't still pending --
    callers translate that into a 404/409."""
    doc = get_bulk_decision(decision_id)
    if doc is None:
        raise ValueError(f"Bulk decision '{decision_id}' not found.")
    if doc["status"] != "pending":
        raise ValueError(f"This proposal is already '{doc['status']}' — it can't be reviewed again.")
    doc["status"] = status
    doc["reviewed_by"] = reviewed_by
    doc["reviewed_at"] = _now()
    if extra:
        doc.update(extra)
    return update_bulk_decision(doc)


# ---------------------------------------------------------------------------
# users (real accounts -- see auth.py. password_hash is a bcrypt hash,
# never a plaintext password; nothing in this module or api_blueprint.py
# ever returns password_hash to a client -- see _safe_user() there.)
# ---------------------------------------------------------------------------
def create_user(username: str, password_hash: str, display_name: str, email: str, role_keys: list, created_by: str) -> dict:
    doc = {
        "id": str(uuid.uuid4()),
        "username": username.strip().lower(),
        "password_hash": password_hash,
        "display_name": display_name,
        "email": email,
        "role_keys": list(role_keys),
        "status": "active",
        "created_at": _now(),
        "created_by": created_by,
        "updated_at": _now(),
    }
    return get_container("users").upsert_item(doc)


def get_user(user_id: str):
    from azure.cosmos.exceptions import CosmosResourceNotFoundError

    try:
        return get_container("users").read_item(item=user_id, partition_key=user_id)
    except CosmosResourceNotFoundError:
        return None


def get_user_by_username(username: str):
    container = get_container("users")
    items = list(
        container.query_items(
            query="SELECT * FROM c WHERE c.username = @u",
            parameters=[{"name": "@u", "value": username.strip().lower()}],
            enable_cross_partition_query=True,
        )
    )
    return items[0] if items else None


def list_users() -> list:
    container = get_container("users")
    return list(container.query_items(query="SELECT * FROM c ORDER BY c.created_at ASC", enable_cross_partition_query=True))


def update_user(doc: dict) -> dict:
    """Caller passes the full document back (read, mutate, save) -- same
    convention as update_bulk_decision()."""
    doc["updated_at"] = _now()
    return get_container("users").upsert_item(doc)


def count_active_users_with_role(role_key: str, exclude_user_id: str = None) -> int:
    """Used to enforce "protect the last active Admin" -- see
    api_blueprint.py's safeguards before disabling a user or removing a
    role from one."""
    users = [u for u in list_users() if u.get("status") == "active" and role_key in u.get("role_keys", [])]
    if exclude_user_id:
        users = [u for u in users if u["id"] != exclude_user_id]
    return len(users)


# ---------------------------------------------------------------------------
# roles (role_key is the Cosmos id -- role keys are stable identifiers like
# "admin"/"recruiter", not random UUIDs, since they're referenced
# directly by users.role_keys and by code in a few places like
# auth.DEFAULT_ROLES). Permission *definitions* are the static auth.PERMISSIONS
# catalog, not stored here -- only which permissions each role grants.
# ---------------------------------------------------------------------------
def create_role(role_key: str, display_name: str, description: str, permission_ids: list, is_system: bool = False) -> dict:
    doc = {
        "id": role_key,
        "role_key": role_key,
        "display_name": display_name,
        "description": description,
        "is_system": is_system,
        "permission_ids": list(permission_ids),
        "created_at": _now(),
        "updated_at": _now(),
    }
    return get_container("roles").upsert_item(doc)


def get_role(role_key: str):
    from azure.cosmos.exceptions import CosmosResourceNotFoundError

    try:
        return get_container("roles").read_item(item=role_key, partition_key=role_key)
    except CosmosResourceNotFoundError:
        return None


def list_roles() -> list:
    container = get_container("roles")
    return list(container.query_items(query="SELECT * FROM c ORDER BY c.display_name ASC", enable_cross_partition_query=True))


def update_role(doc: dict) -> dict:
    doc["updated_at"] = _now()
    return get_container("roles").upsert_item(doc)


def ensure_default_roles() -> list:
    """Seeds auth.DEFAULT_ROLES into the roles container on first run
    (idempotent -- upsert, so re-running after an Admin edits a custom
    role's description/permissions does NOT clobber their edit, since it
    only inserts roles that don't exist yet -- system roles are read-only
    anyway, see api_blueprint.py's update_role_endpoint). Called once at
    Function App startup (function_app.py) and also safe to call from
    tests/scripts."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
    from auth import DEFAULT_ROLES  # noqa: E402

    created = []
    for role in DEFAULT_ROLES:
        if get_role(role["role_key"]) is None:
            create_role(role["role_key"], role["display_name"], role["description"], role["permission_ids"], is_system=True)
            created.append(role["role_key"])
    return created


# ---------------------------------------------------------------------------
# pages (the Dynamic Page Access Management registry -- see auth.PAGES'
# docstring for why this app's "route registry" is a code-defined catalog
# rather than something scanned from a client-side router that doesn't
# exist here) and page_access_policies (per page+role access decisions,
# deny-by-default -- see auth.effective_pages()'s docstring).
# ---------------------------------------------------------------------------
def get_page(page_id: str):
    from azure.cosmos.exceptions import CosmosResourceNotFoundError

    try:
        return get_container("pages").read_item(item=page_id, partition_key=page_id)
    except CosmosResourceNotFoundError:
        return None


def list_pages() -> list:
    container = get_container("pages")
    return list(container.query_items(query="SELECT * FROM c ORDER BY c.module ASC, c.name ASC", enable_cross_partition_query=True))


def update_page(doc: dict) -> dict:
    """Caller passes the full document back (read via get_page(), mutate,
    then save) -- same upsert-the-whole-thing convention as update_user()/
    update_role()."""
    return get_container("pages").upsert_item(doc)


def _page_access_policy_id(page_id: str, role_key: str) -> str:
    return f"{page_id}:{role_key}"


def get_page_access_policies(page_id: str) -> list:
    container = get_container("page_access_policies")
    return list(
        container.query_items(
            query="SELECT * FROM c WHERE c.page_id = @pid",
            parameters=[{"name": "@pid", "value": page_id}],
            enable_cross_partition_query=True,
        )
    )


def list_all_page_access_policies() -> list:
    container = get_container("page_access_policies")
    return list(container.query_items(query="SELECT * FROM c", enable_cross_partition_query=True))


def set_page_access(page_id: str, role_key: str, allowed: bool, actor: str) -> dict:
    """Upsert-to-update, keyed by "{page_id}:{role_key}" -- a page/role pair
    always has exactly one policy document, so re-saving the same
    configuration never creates a duplicate or conflicting record."""
    doc_id = _page_access_policy_id(page_id, role_key)
    container = get_container("page_access_policies")
    from azure.cosmos.exceptions import CosmosResourceNotFoundError

    try:
        existing = container.read_item(item=doc_id, partition_key=doc_id)
        created_by = existing.get("created_by", actor)
    except CosmosResourceNotFoundError:
        created_by = actor

    doc = {
        "id": doc_id, "page_id": page_id, "role_key": role_key, "allowed": bool(allowed),
        "created_by": created_by, "updated_by": actor, "updated_at": _now(),
    }
    return container.upsert_item(doc)


def sync_pages() -> dict:
    """Reconciles the code-defined route registry (auth.PAGES -- this app's
    "route-registration mechanism", see that list's docstring) against the
    persisted `pages` container. THIS is the discovery mechanism: a page
    only has to exist in auth.PAGES (added by a developer alongside the
    App.jsx nav entry it describes) to be picked up here -- nobody has to
    hand-create a database row for it first.

    Returns {"new": [...], "updated": [...], "stale": [...], "first_run": bool}
    (page_id lists) for the caller (api_blueprint.py's sync_pages_endpoint)
    to audit-log and for the Admin Panel to surface as "newly discovered"
    / "metadata changed" / "stale".

    SECURE DEFAULTS: a brand new page_id gets NO page_access_policies at
    all here -- deny-by-default, per the spec. The one exception is the
    very first time this ever runs against a completely empty `pages`
    container: that run seeds auth.default_page_access_policies(), which
    reproduces the access every role already implicitly had via its
    operation permissions -- so shipping this feature doesn't regress
    every existing page to "nobody can reach it" on day one. Any page
    added to auth.PAGES after that first run starts with zero policies.

    REDISCOVERY PRESERVES POLICIES: this only ever upserts a page's
    *metadata* fields -- it never writes to page_access_policies for a
    page that already has any, so an admin's saved configuration survives
    being "rediscovered" on every later call.

    STALE PAGES: a page_id present in the persisted container but absent
    from auth.PAGES now is marked discovery_status="stale", never
    deleted -- its policies are left untouched too, in case the page
    comes back (e.g. a route temporarily missing from a dev build)."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
    from auth import PAGES, PAGE_IDS, default_page_access_policies  # noqa: E402

    container = get_container("pages")
    existing_docs = list_pages()
    existing_by_id = {d["page_id"]: d for d in existing_docs}
    is_first_run = len(existing_docs) == 0
    now = _now()

    new_ids, updated_ids, stale_ids = [], [], []
    tracked_fields = ("name", "route", "module", "required_permission", "is_protected", "route_type")

    for page in PAGES:
        pid = page["page_id"]
        prior = existing_by_id.get(pid)
        # On the very first run, every page is about to get seeded with
        # policies below (default_page_access_policies()) -- treat it as
        # "will have policies" now so the status this call returns matches
        # what a GET right after it would show, rather than lagging one
        # sync call behind.
        has_policies = is_first_run or bool(get_page_access_policies(pid))
        doc = {
            "id": pid, "page_id": pid, "name": page["name"], "route": page["route"],
            "module": page["module"], "description": page.get("description", ""),
            "required_permission": page.get("required_permission"),
            "route_type": page.get("route_type", "page"), "is_protected": page.get("is_protected", True),
            "discovery_status": "configured" if has_policies else "discovered",
            "last_discovered_at": now,
            "first_discovered_at": (prior or {}).get("first_discovered_at", now),
        }
        if prior is None:
            new_ids.append(pid)
        elif any(prior.get(f) != doc[f] for f in tracked_fields):
            updated_ids.append(pid)
        container.upsert_item(doc)

    for pid, prior in existing_by_id.items():
        if pid not in PAGE_IDS and prior.get("discovery_status") != "stale":
            prior["discovery_status"] = "stale"
            container.upsert_item(prior)
            stale_ids.append(pid)

    if is_first_run:
        for policy in default_page_access_policies():
            set_page_access(policy["page_id"], policy["role_key"], policy["allowed"], actor="system:initial_seed")

    return {"new": new_ids, "updated": updated_ids, "stale": stale_ids, "first_run": is_first_run}
