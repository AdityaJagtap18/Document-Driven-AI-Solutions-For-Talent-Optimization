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


def delete_candidate(candidate_id: str) -> bool:
    """F-14 (candidate data erasure, minimal slice): deletes the primary
    candidate document. Returns True if a document existed and was
    deleted, False if there was nothing to delete (not an error --
    erasure of an already-absent record is a no-op success, not a
    failure)."""
    from azure.cosmos.exceptions import CosmosResourceNotFoundError

    try:
        get_container("candidates").delete_item(item=candidate_id, partition_key=candidate_id)
        return True
    except CosmosResourceNotFoundError:
        return False


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


def assign_interviewers(candidate_id: str, usernames: list, actor: str):
    """Sets the record-level access scope for the 'interviewer' role on one
    candidate (see candidate.schema.json's assigned_interviewer_usernames
    field and auth.py's interviews.assign permission). Replaces the whole
    list, same semantics as update_role_endpoint's role_keys replace --
    the caller sends the full intended assignment, not a delta. Returns
    None if the candidate doesn't exist (caller maps that to 404)."""
    candidate = get_candidate(candidate_id)
    if candidate is None:
        return None
    candidate["assigned_interviewer_usernames"] = list(usernames)
    upsert_candidate(candidate)
    append_audit_log({
        "candidate_id": candidate_id, "action": "interviewer_assigned",
        "assigned_interviewer_usernames": list(usernames), "updated_by": actor,
    })
    return candidate


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


def delete_interview_transcripts_for_candidate(candidate_id: str) -> int:
    """F-14 (candidate data erasure, minimal slice): deletes every real
    transcript document for this candidate. Returns the real count
    deleted (0 if none existed -- not an error)."""
    container = get_container("interview_transcripts")
    transcripts = list_interview_transcripts(candidate_id)
    for t in transcripts:
        container.delete_item(item=t["id"], partition_key=candidate_id)
    return len(transcripts)


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


def delete_scoring_history_for_candidate(candidate_id: str) -> int:
    """F-14 (candidate data erasure, minimal slice): deletes every real
    scoring-history record for this candidate. Returns the real count
    deleted (0 if none existed)."""
    container = get_container("scoring_history")
    records = list_scoring_history(candidate_id)
    for r in records:
        container.delete_item(item=r["id"], partition_key=candidate_id)
    return len(records)


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


def find_bulk_decisions_referencing_candidate(candidate_id: str) -> list:
    """F-14 (candidate data erasure, minimal slice): read-only search for
    bulk-decision batches that include this candidate_id in their
    candidate_ids list. Deliberately NOT a deletion function -- a bulk
    decision's candidate_ids array is shared with OTHER real candidates'
    ids in the same batch, so removing the whole document would erase
    data belonging to people who were never asked to be erased. Reported
    to the caller as a real, found, NOT-automatically-modified location,
    per this feature's own "document the limitation... rather than
    falsely claiming complete erasure" requirement -- out of scope for
    this minimal slice to safely auto-redact."""
    container = get_container("bulk_decisions")
    return list(
        container.query_items(
            query="SELECT c.id, c.decision, c.status, c.proposed_at FROM c WHERE ARRAY_CONTAINS(c.candidate_ids, @cid)",
            parameters=[{"name": "@cid", "value": candidate_id}],
            enable_cross_partition_query=True,
        )
    )


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


def delete_user(user_id: str) -> bool:
    """Permanently deletes a user account document -- a real, irreversible
    hard delete, not a status flip (see update_user()/set_user_status_
    endpoint for the reversible alternative, which is what day-to-day
    account removal should normally use). Returns True if a document
    existed and was deleted, False if there was nothing to delete (same
    "erasure of an already-absent record is a no-op success" convention
    as delete_candidate())."""
    from azure.cosmos.exceptions import CosmosResourceNotFoundError

    try:
        get_container("users").delete_item(item=user_id, partition_key=user_id)
        return True
    except CosmosResourceNotFoundError:
        return False


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


def sync_default_roles() -> list:
    """Real, previously-missing counterpart to ensure_default_roles():
    that function only INSERTS a system role that doesn't exist in Cosmos
    yet -- a system role seeded before a later code change adds a new
    permission to its auth.DEFAULT_ROLES entry (e.g. recruiter gaining
    jds.create for Job Description Management) silently keeps its STALE
    Cosmos permission_ids forever, since nothing ever re-upserts it. This
    reconciles every is_system role's permission_ids/display_name/
    description against the current code-defined catalog (mirroring
    sync_pages()'s own "reconcile metadata against the code registry"
    pattern) -- never touches a NON-system (admin-created custom) role.
    Returns the list of role_keys that were actually out of sync."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
    from auth import DEFAULT_ROLES  # noqa: E402

    updated = []
    for role in DEFAULT_ROLES:
        existing = get_role(role["role_key"])
        if existing is None:
            create_role(role["role_key"], role["display_name"], role["description"], role["permission_ids"], is_system=True)
            updated.append(role["role_key"])
            continue
        if not existing.get("is_system"):
            continue
        if (sorted(existing.get("permission_ids", [])) != sorted(role["permission_ids"])
                or existing.get("display_name") != role["display_name"]
                or existing.get("description") != role["description"]):
            existing["permission_ids"] = list(role["permission_ids"])
            existing["display_name"] = role["display_name"]
            existing["description"] = role["description"]
            update_role(existing)
            updated.append(role["role_key"])
    return updated


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


# ---------------------------------------------------------------------------
# Azure Service Hub: integration instances (one per adapter_type this app
# actually uses -- see azure_integrations.ADAPTER_TYPES) and health-check
# HISTORY (not just the latest result). Registering an instance never
# stores a secret value -- only non-secret metadata (display name,
# environment, and, for resource discovery, a subscription_id/
# resource_group scope); the real credential always comes from
# secrets_loader at check/discovery time, unchanged from before this
# feature. See azure_integrations.py's module docstring for the full
# security rationale.
# ---------------------------------------------------------------------------
def create_integration_instance(integration_id: str, adapter_type: str, display_name: str, environment: str,
                                 config: dict, created_by: str) -> dict:
    doc = {
        "id": integration_id, "integration_id": integration_id, "adapter_type": adapter_type,
        "display_name": display_name, "environment": environment, "config": config or {},
        "config_version": 1, "enabled": False,  # never enabled at registration -- see api_blueprint.py's enable endpoint
        "lifecycle_status": "draft",  # draft -> active (on enable) -> disabled; see the lifecycle doc comment on set_azure_integration_enabled_endpoint
        "last_connection_outcome": None, "last_health_checked_at": None, "last_failure": None,
        "created_by": created_by, "created_at": _now(), "updated_by": created_by, "updated_at": _now(),
    }
    created = get_container("integration_instances").upsert_item(doc)
    record_integration_config_change(integration_id, "registered", created_by, {"adapter_type": adapter_type, "environment": environment, "config_version": 1})
    return created


def get_integration_instance(integration_id: str):
    from azure.cosmos.exceptions import CosmosResourceNotFoundError

    try:
        return get_container("integration_instances").read_item(item=integration_id, partition_key=integration_id)
    except CosmosResourceNotFoundError:
        return None


def list_integration_instances() -> list:
    container = get_container("integration_instances")
    return list(container.query_items(query="SELECT * FROM c ORDER BY c.display_name ASC", enable_cross_partition_query=True))


def update_integration_instance(doc: dict) -> dict:
    """Caller passes the full document back (read, mutate, save) -- same
    convention as update_user()/update_role()/update_page(). Does NOT
    bump config_version/updated_at itself -- callers that change config
    do that explicitly (see api_blueprint.py's optimistic-concurrency
    check), so a status-only update (e.g. after a health check) doesn't
    look like a configuration change in the audit trail."""
    return get_container("integration_instances").upsert_item(doc)


def record_integration_health_check(integration_id: str, result: dict) -> dict:
    """Appends one health-check record to history (never overwritten) AND
    updates the owning instance's cached last_connection_outcome/
    last_health_checked_at, so the inventory list can show current status
    without re-running a check for every row."""
    doc = dict(result)
    doc["id"] = str(uuid.uuid4())
    doc["integration_id"] = integration_id
    get_container("integration_health_checks").upsert_item(doc)

    instance = get_integration_instance(integration_id)
    if instance is not None:
        instance["last_connection_outcome"] = result.get("outcome")
        instance["last_health_checked_at"] = result.get("checked_at")
        # Distinct "last failure" summary (redacted, same fields as the health-check
        # record) for quick display without re-querying history -- cleared on a
        # healthy outcome rather than left showing a stale failure.
        instance["last_failure"] = None if result.get("outcome") == "healthy" else {
            "outcome": result.get("outcome"), "error_category": result.get("error_category"),
            "detail": result.get("detail"), "checked_at": result.get("checked_at"),
        }
        update_integration_instance(instance)
    return doc


def record_integration_config_change(integration_id: str, operation: str, actor: str, safe_summary: dict) -> dict:
    """Appends one entry to the configuration CHANGE history (separate
    from health-check history -- see client.py's container comment).
    `safe_summary` must never contain a secret value or resolved
    credential -- callers pass only non-secret field names/booleans/
    versions, same discipline as audit_log entries elsewhere in this
    repo. This is what powers the Configuration History tab and
    supports rollback (restoring a prior config snapshot as a NEW
    version, never mutating history)."""
    doc = {
        "id": str(uuid.uuid4()), "integration_id": integration_id, "operation": operation,
        "actor": actor, "safe_summary": safe_summary or {}, "recorded_at": _now(),
    }
    return get_container("integration_config_history").upsert_item(doc)


def list_integration_config_history(integration_id: str, limit: int = 50) -> list:
    container = get_container("integration_config_history")
    items = list(
        container.query_items(
            query="SELECT * FROM c WHERE c.integration_id = @iid ORDER BY c.recorded_at DESC",
            parameters=[{"name": "@iid", "value": integration_id}],
            partition_key=integration_id,
        )
    )
    return items[:limit]


def list_integration_health_checks(integration_id: str, limit: int = 50) -> list:
    container = get_container("integration_health_checks")
    items = list(
        container.query_items(
            query="SELECT * FROM c WHERE c.integration_id = @iid ORDER BY c.checked_at DESC",
            parameters=[{"name": "@iid", "value": integration_id}],
            partition_key=integration_id,
        )
    )
    return items[:limit]


def ensure_default_integration_instances() -> list:
    """Seeds one pre-registered, pre-enabled IntegrationInstance per
    ADAPTER_TYPES entry on first run -- these are the integrations this
    app was ALREADY using (via secrets_loader/.env) before the Azure
    Service Hub existed; seeding them as already-enabled preserves that
    continuity instead of making every existing integration look
    "unregistered" the moment this feature ships. Idempotent -- only
    creates instances that don't already exist. Called once at Function
    App / bootstrap_iam.py startup, same pattern as
    ensure_default_roles()/sync_pages()."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
    from azure_integrations import ADAPTER_TYPES  # noqa: E402

    created = []
    for adapter_type, adapter in ADAPTER_TYPES.items():
        if adapter.get("status") == "planned":
            continue  # nothing is "already in use" for an adapter with no implementation yet
        integration_id = adapter_type  # one default instance per adapter, same id as the type
        if get_integration_instance(integration_id) is None:
            doc = create_integration_instance(
                integration_id=integration_id, adapter_type=adapter_type, display_name=adapter["name"],
                environment="default", config={}, created_by="system:initial_seed",
            )
            doc["enabled"] = True
            doc["lifecycle_status"] = "active"
            update_integration_instance(doc)
            created.append(integration_id)
    return created


# ---------------------------------------------------------------------------
# RAG Configuration Console -- see ../rag_config.py's module docstring for
# the full rationale. Two singleton documents in rag_configurations:
# id="active" (what ResumeParser.py actually reads) and id="draft" (an
# admin's in-progress edit, consumed on activation). Config CHANGE
# history is separate, same "distinct from a test-run log" pattern as
# integration_config_history.
# ---------------------------------------------------------------------------
def get_active_rag_config():
    from azure.cosmos.exceptions import CosmosResourceNotFoundError

    try:
        return get_container("rag_configurations").read_item(item="active", partition_key="active")
    except CosmosResourceNotFoundError:
        return None


def get_draft_rag_config():
    from azure.cosmos.exceptions import CosmosResourceNotFoundError

    try:
        return get_container("rag_configurations").read_item(item="draft", partition_key="draft")
    except CosmosResourceNotFoundError:
        return None


def save_draft_rag_config(fields: dict, actor: str) -> dict:
    """Creates or overwrites the single draft document. `fields` is the
    full {chunking, retrieval, generation} dict the caller wants to try
    -- validated by the caller (rag_config.validate_config()) before this
    is called, since this function just persists, it doesn't judge."""
    active = get_active_rag_config()
    doc = {
        "id": "draft", "lifecycle_status": "draft",
        "config_version": (active["config_version"] + 1) if active else 1,
        "chunking": fields["chunking"], "retrieval": fields["retrieval"], "generation": fields["generation"],
        "created_by": actor, "created_at": _now(), "updated_by": actor, "updated_at": _now(),
    }
    saved = get_container("rag_configurations").upsert_item(doc)
    record_rag_config_change("draft_saved", actor, {"config_version": doc["config_version"]})
    return saved


def activate_rag_config(actor: str) -> dict:
    """Promotes the current draft to active -- caller must have already
    validated it (rag_config.validate_config()). The draft is deleted
    afterward (consumed), so GET .../draft honestly reports "no pending
    draft" until an admin starts a new edit -- never a stale draft that
    looks unreviewed forever."""
    draft = get_draft_rag_config()
    if draft is None:
        raise ValueError("No draft configuration exists to activate.")
    active_doc = {
        "id": "active", "lifecycle_status": "active", "config_version": draft["config_version"],
        "chunking": draft["chunking"], "retrieval": draft["retrieval"], "generation": draft["generation"],
        "created_by": draft["created_by"], "created_at": draft["created_at"],
        "updated_by": actor, "updated_at": _now(),
    }
    saved = get_container("rag_configurations").upsert_item(active_doc)
    get_container("rag_configurations").delete_item(item="draft", partition_key="draft")
    record_rag_config_change("activated", actor, {
        "config_version": saved["config_version"],
        "chunking": saved["chunking"], "retrieval": saved["retrieval"], "generation": saved["generation"],
    })
    return saved


def record_rag_config_change(operation: str, actor: str, safe_summary: dict) -> dict:
    doc = {"id": str(uuid.uuid4()), "operation": operation, "actor": actor, "safe_summary": safe_summary or {}, "recorded_at": _now()}
    return get_container("rag_config_history").upsert_item(doc)


def list_rag_config_history(limit: int = 50) -> list:
    container = get_container("rag_config_history")
    items = list(container.query_items(query="SELECT * FROM c ORDER BY c.recorded_at DESC", enable_cross_partition_query=True))
    return items[:limit]


# ---------------------------------------------------------------------------
# tokenomics_config (F-02, minimal slice) -- the real budget/alert
# configuration, same Draft -> Activate -> History lifecycle as
# rag_configurations above (single active/draft documents, versioned
# history, never silently applied). See tokenomics.py for the config
# shape/validation and the real usage/budget-check logic this feeds.
# ---------------------------------------------------------------------------
def get_active_tokenomics_config():
    from azure.cosmos.exceptions import CosmosResourceNotFoundError

    try:
        return get_container("tokenomics_config").read_item(item="active", partition_key="active")
    except CosmosResourceNotFoundError:
        return None


def get_draft_tokenomics_config():
    from azure.cosmos.exceptions import CosmosResourceNotFoundError

    try:
        return get_container("tokenomics_config").read_item(item="draft", partition_key="draft")
    except CosmosResourceNotFoundError:
        return None


def save_draft_tokenomics_config(fields: dict, actor: str) -> dict:
    """`fields` is the full budget-config dict the caller wants to try --
    validated by the caller (tokenomics.validate_config()) before this is
    called, same convention as save_draft_rag_config()."""
    active = get_active_tokenomics_config()
    doc = {
        "id": "draft", "lifecycle_status": "draft",
        "config_version": (active["config_version"] + 1) if active else 1,
        "monthly_budget_usd": fields.get("monthly_budget_usd"),
        "warning_threshold_pct": fields["warning_threshold_pct"],
        "created_by": actor, "created_at": _now(), "updated_by": actor, "updated_at": _now(),
    }
    saved = get_container("tokenomics_config").upsert_item(doc)
    record_tokenomics_config_change("draft_saved", actor, {"config_version": doc["config_version"]})
    return saved


def activate_tokenomics_config(actor: str) -> dict:
    """Promotes the current draft to active -- caller must have already
    validated it. The draft is deleted afterward (consumed), same
    convention as activate_rag_config()."""
    draft = get_draft_tokenomics_config()
    if draft is None:
        raise ValueError("No draft configuration exists to activate.")
    active_doc = {
        "id": "active", "lifecycle_status": "active", "config_version": draft["config_version"],
        "monthly_budget_usd": draft["monthly_budget_usd"], "warning_threshold_pct": draft["warning_threshold_pct"],
        "created_by": draft["created_by"], "created_at": draft["created_at"],
        "updated_by": actor, "updated_at": _now(),
    }
    saved = get_container("tokenomics_config").upsert_item(active_doc)
    get_container("tokenomics_config").delete_item(item="draft", partition_key="draft")
    record_tokenomics_config_change("activated", actor, {
        "config_version": saved["config_version"],
        "monthly_budget_usd": saved["monthly_budget_usd"], "warning_threshold_pct": saved["warning_threshold_pct"],
    })
    return saved


def record_tokenomics_config_change(operation: str, actor: str, safe_summary: dict) -> dict:
    doc = {"id": str(uuid.uuid4()), "operation": operation, "actor": actor, "safe_summary": safe_summary or {}, "recorded_at": _now()}
    return get_container("tokenomics_config_history").upsert_item(doc)


def list_tokenomics_config_history(limit: int = 50) -> list:
    container = get_container("tokenomics_config_history")
    items = list(container.query_items(query="SELECT * FROM c ORDER BY c.recorded_at DESC", enable_cross_partition_query=True))
    return items[:limit]


# ---------------------------------------------------------------------------
# RAG prompt versions -- one container, partitioned by prompt_id, holding
# EVERY version ever created (never deleted). Exactly one version per
# prompt_id may have status "active" at a time; activating a new one
# demotes the previous active version to "superseded", never deletes it.
# ---------------------------------------------------------------------------
def list_rag_prompt_versions(prompt_id: str) -> list:
    container = get_container("rag_prompt_versions")
    items = list(
        container.query_items(
            query="SELECT * FROM c WHERE c.prompt_id = @pid ORDER BY c.version DESC",
            parameters=[{"name": "@pid", "value": prompt_id}],
            partition_key=prompt_id,
        )
    )
    return items


def get_active_rag_prompt_version(prompt_id: str):
    versions = list_rag_prompt_versions(prompt_id)
    return next((v for v in versions if v["status"] == "active"), None)


def create_rag_prompt_draft_version(prompt_id: str, template: str, actor: str) -> dict:
    existing = list_rag_prompt_versions(prompt_id)
    next_version = (max((v["version"] for v in existing), default=0)) + 1
    doc = {
        "id": f"{prompt_id}:v{next_version}", "prompt_id": prompt_id, "version": next_version,
        "template": template, "status": "draft", "created_by": actor, "created_at": _now(),
    }
    saved = get_container("rag_prompt_versions").upsert_item(doc)
    record_rag_config_change("prompt_draft_created", actor, {"prompt_id": prompt_id, "version": next_version})
    return saved


def activate_rag_prompt_version(prompt_id: str, version: int, actor: str) -> dict:
    """Marks `version` active and demotes whichever version was
    previously active (if any) to "superseded" -- never deletes a
    version, so every prompt text that was ever live stays inspectable
    and reusable for a future rollback."""
    versions = list_rag_prompt_versions(prompt_id)
    target = next((v for v in versions if v["version"] == version), None)
    if target is None:
        raise ValueError(f"Prompt '{prompt_id}' has no version {version}.")

    container = get_container("rag_prompt_versions")
    for v in versions:
        if v["status"] == "active" and v["version"] != version:
            v["status"] = "superseded"
            container.upsert_item(v)
    target["status"] = "active"
    saved = container.upsert_item(target)
    record_rag_config_change("prompt_activated", actor, {"prompt_id": prompt_id, "version": version})
    return saved


#  Communications Configuration Console -- see ../comms_config.py's module
# docstring. Same Draft/Active singleton + separate change-history pattern
# as rag_configurations/rag_config_history, duplicated here on purpose
# (not refactored into one shared generic function) because the two
# config shapes (comms vs. interview-bot) are deliberately independent
# per the spec's "keep them separate" requirement -- a shared helper
# would recouple them.
# ---------------------------------------------------------------------------
def get_active_comms_config():
    from azure.cosmos.exceptions import CosmosResourceNotFoundError

    try:
        return get_container("comms_configurations").read_item(item="active", partition_key="active")
    except CosmosResourceNotFoundError:
        return None


def get_draft_comms_config():
    from azure.cosmos.exceptions import CosmosResourceNotFoundError

    try:
        return get_container("comms_configurations").read_item(item="draft", partition_key="draft")
    except CosmosResourceNotFoundError:
        return None


def save_draft_comms_config(fields: dict, actor: str) -> dict:
    active = get_active_comms_config()
    doc = {
        "id": "draft", "lifecycle_status": "draft",
        "config_version": (active["config_version"] + 1) if active else 1,
        "email": fields["email"], "whatsapp": fields["whatsapp"], "delivery_policies": fields["delivery_policies"],
        "created_by": actor, "created_at": _now(), "updated_by": actor, "updated_at": _now(),
    }
    saved = get_container("comms_configurations").upsert_item(doc)
    record_comms_config_change("draft_saved", actor, {"config_version": doc["config_version"]})
    return saved


def activate_comms_config(actor: str) -> dict:
    draft = get_draft_comms_config()
    if draft is None:
        raise ValueError("No draft configuration exists to activate.")
    active_doc = {
        "id": "active", "lifecycle_status": "active", "config_version": draft["config_version"],
        "email": draft["email"], "whatsapp": draft["whatsapp"], "delivery_policies": draft["delivery_policies"],
        "created_by": draft["created_by"], "created_at": draft["created_at"],
        "updated_by": actor, "updated_at": _now(),
    }
    saved = get_container("comms_configurations").upsert_item(active_doc)
    get_container("comms_configurations").delete_item(item="draft", partition_key="draft")
    record_comms_config_change("activated", actor, {
        "config_version": saved["config_version"], "email": saved["email"],
        "whatsapp": saved["whatsapp"], "delivery_policies": saved["delivery_policies"],
    })
    return saved


def record_comms_config_change(operation: str, actor: str, safe_summary: dict) -> dict:
    doc = {"id": str(uuid.uuid4()), "operation": operation, "actor": actor, "safe_summary": safe_summary or {}, "recorded_at": _now()}
    return get_container("comms_config_history").upsert_item(doc)


def list_comms_config_history(limit: int = 50) -> list:
    container = get_container("comms_config_history")
    items = list(container.query_items(query="SELECT * FROM c ORDER BY c.recorded_at DESC", enable_cross_partition_query=True))
    return items[:limit]


def get_active_interview_bot_config():
    from azure.cosmos.exceptions import CosmosResourceNotFoundError

    try:
        return get_container("interview_bot_configurations").read_item(item="active", partition_key="active")
    except CosmosResourceNotFoundError:
        return None


def get_draft_interview_bot_config():
    from azure.cosmos.exceptions import CosmosResourceNotFoundError

    try:
        return get_container("interview_bot_configurations").read_item(item="draft", partition_key="draft")
    except CosmosResourceNotFoundError:
        return None


def save_draft_interview_bot_config(fields: dict, actor: str) -> dict:
    active = get_active_interview_bot_config()
    doc = dict(fields)
    doc["id"] = "draft"
    doc["lifecycle_status"] = "draft"
    doc["config_version"] = (active["config_version"] + 1) if active else 1
    doc["created_by"] = actor
    doc["created_at"] = _now()
    doc["updated_by"] = actor
    doc["updated_at"] = _now()
    saved = get_container("interview_bot_configurations").upsert_item(doc)
    record_comms_config_change("interview_bot_draft_saved", actor, {"config_version": doc["config_version"]})
    return saved


def activate_interview_bot_config(actor: str) -> dict:
    draft = get_draft_interview_bot_config()
    if draft is None:
        raise ValueError("No draft interview bot configuration exists to activate.")
    active_doc = dict(draft)
    active_doc["id"] = "active"
    active_doc["lifecycle_status"] = "active"
    active_doc["updated_by"] = actor
    active_doc["updated_at"] = _now()
    saved = get_container("interview_bot_configurations").upsert_item(active_doc)
    get_container("interview_bot_configurations").delete_item(item="draft", partition_key="draft")
    record_comms_config_change("interview_bot_activated", actor, {"config_version": saved["config_version"]})
    return saved


def list_comms_template_versions(template_id: str) -> list:
    container = get_container("comms_template_versions")
    return list(
        container.query_items(
            query="SELECT * FROM c WHERE c.template_id = @tid ORDER BY c.version DESC",
            parameters=[{"name": "@tid", "value": template_id}],
            partition_key=template_id,
        )
    )


def get_active_comms_template_version(template_id: str):
    versions = list_comms_template_versions(template_id)
    return next((v for v in versions if v["status"] == "active"), None)


def create_comms_template_draft_version(template_id: str, subject: str, body: str, actor: str) -> dict:
    existing = list_comms_template_versions(template_id)
    next_version = (max((v["version"] for v in existing), default=0)) + 1
    doc = {
        "id": f"{template_id}:v{next_version}", "template_id": template_id, "version": next_version,
        "subject": subject, "body": body, "status": "draft", "created_by": actor, "created_at": _now(),
    }
    saved = get_container("comms_template_versions").upsert_item(doc)
    record_comms_config_change("template_draft_created", actor, {"template_id": template_id, "version": next_version})
    return saved


def activate_comms_template_version(template_id: str, version: int, actor: str) -> dict:
    versions = list_comms_template_versions(template_id)
    target = next((v for v in versions if v["version"] == version), None)
    if target is None:
        raise ValueError(f"Template '{template_id}' has no version {version}.")
    container = get_container("comms_template_versions")
    for v in versions:
        if v["status"] == "active" and v["version"] != version:
            v["status"] = "superseded"
            container.upsert_item(v)
    target["status"] = "active"
    saved = container.upsert_item(target)
    record_comms_config_change("template_activated", actor, {"template_id": template_id, "version": version})
    return saved


def ensure_default_comms_templates() -> list:
    """Seeds version 1 (already active) of each comms_config.DEFAULT_TEMPLATES
    entry from the real existing template text -- same "reuse existing
    templates" rule as ensure_default_rag_prompts()."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
    from comms_config import DEFAULT_TEMPLATES  # noqa: E402

    seeded = []
    for template_id, defaults in DEFAULT_TEMPLATES.items():
        if list_comms_template_versions(template_id):
            continue
        doc = create_comms_template_draft_version(template_id, defaults.get("subject", ""), defaults["body"], "system:initial_seed")
        activate_comms_template_version(template_id, doc["version"], "system:initial_seed")
        seeded.append(template_id)
    return seeded


def record_comms_delivery(entry: dict) -> dict:
    """One document per real send ATTEMPT (never a connectivity check).
    `entry` must include correlation_id (the candidate_id, this
    container's partition key), channel, provider, submitted_at,
    outcome ("submitted"/"provider_accepted"/"failed"/"status_unavailable"),
    and a redacted provider_response snippet -- never a raw credential,
    phone number, or email address beyond what's already elsewhere in
    the candidate record."""
    doc = dict(entry)
    doc["id"] = str(uuid.uuid4())
    return get_container("comms_delivery_log").upsert_item(doc)


def list_comms_deliveries(correlation_id: str, limit: int = 50) -> list:
    container = get_container("comms_delivery_log")
    items = list(
        container.query_items(
            query="SELECT * FROM c WHERE c.correlation_id = @cid ORDER BY c.submitted_at DESC",
            parameters=[{"name": "@cid", "value": correlation_id}],
            partition_key=correlation_id,
        )
    )
    return items[:limit]


def delete_comms_deliveries_for_candidate(candidate_id: str) -> int:
    """F-14 (candidate data erasure, minimal slice): deletes every real
    communications-delivery record for this candidate. correlation_id IS
    the candidate_id, this container's partition key (see
    record_comms_delivery()'s docstring) -- not a separate lookup.
    Returns the real count deleted (0 if none existed)."""
    container = get_container("comms_delivery_log")
    records = list_comms_deliveries(candidate_id, limit=10000)
    for r in records:
        container.delete_item(item=r["id"], partition_key=candidate_id)
    return len(records)


def list_all_comms_deliveries(limit: int = 500) -> list:
    """Cross-partition read across every candidate's delivery records --
    unlike list_comms_deliveries() above, scoped to one correlation_id.
    Backs the Communication Delivery Report (aggregate, not per-
    candidate). Same query-shape as list_all_audit_log()."""
    container = get_container("comms_delivery_log")
    items = list(
        container.query_items(
            query="SELECT * FROM c ORDER BY c.submitted_at DESC",
            enable_cross_partition_query=True,
        )
    )
    return items[:limit]


def has_recent_delivery(correlation_id: str, channel: str, workflow: str, within_minutes: int) -> bool:
    """Duplicate-send protection: True if a delivery for this
    (candidate, channel, workflow) was already attempted within the
    last `within_minutes` -- checked by callers BEFORE sending, not
    enforced here (this is a read-only query, sending is the caller's
    decision)."""
    from datetime import datetime, timedelta, timezone

    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=within_minutes)).isoformat()
    deliveries = list_comms_deliveries(correlation_id, limit=50)
    return any(
        d.get("channel") == channel and d.get("workflow") == workflow and d.get("submitted_at", "") >= cutoff
        for d in deliveries
    )


def ensure_default_rag_prompts() -> list:
    """Seeds version 1 of each PROMPT_DEFAULTS entry, already ACTIVE, from
    the exact template text ResumeParser.py has always hardcoded (rag_
    config.py's DEFAULT_*_TEMPLATE constants) -- "reuse existing prompt
    templates" per the spec, not a blank slate. Idempotent -- only seeds
    a prompt_id that has no versions yet. Called once at bootstrap, same
    pattern as ensure_default_integration_instances()."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
    from rag_config import PROMPT_DEFAULTS  # noqa: E402

    seeded = []
    for prompt_id, defaults in PROMPT_DEFAULTS.items():
        if list_rag_prompt_versions(prompt_id):
            continue
        doc = create_rag_prompt_draft_version(prompt_id, defaults["template"], "system:initial_seed")
        activate_rag_prompt_version(prompt_id, doc["version"], "system:initial_seed")
        seeded.append(prompt_id)
    return seeded


def ensure_demo_personas() -> list:
    """OPT-IN ONLY -- unlike every ensure_default_*() above, this is never
    called automatically at bootstrap (see bootstrap_iam.py's
    --create-demo-personas flag, the only caller). Creates one account per
    persona_catalogue.PERSONAS entry (persona_catalogue.py, CDPI Product
    Personas doc Section 8) with a random generated password, so these
    demo accounts are never usable until whoever ran the flag retrieves
    the printed passwords. Each account is explicitly marked is_demo_persona
    so they remain visibly distinguishable from real user accounts created
    through the normal user-management flow (POST /admin/users) -- the
    spec's explicit "must not be treated as real users" requirement.
    Idempotent -- a persona whose demo username already exists is skipped,
    never recreated or password-reset.

    Returns a list of {"username", "password", "role_key", "persona_id"}
    for accounts actually created this call -- the only moment a plaintext
    password is available; callers must print/relay it immediately, it is
    never stored or retrievable again (same one-time-reveal convention as
    secrets_registry.update_secret_value)."""
    import secrets as _secrets

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
    from auth import hash_password  # noqa: E402
    import persona_catalogue  # noqa: E402

    created = []
    for persona in persona_catalogue.PERSONAS:
        username = f"demo_{persona['role_key']}"
        if get_user_by_username(username) is not None:
            continue
        password = _secrets.token_urlsafe(16)
        doc = {
            "id": str(uuid.uuid4()),
            "username": username,
            "password_hash": hash_password(password),
            "display_name": persona["demo_display_name"],
            "email": f"demo+{persona['role_key']}@example.invalid",
            "role_keys": [persona["role_key"]],
            "status": "active",
            "is_demo_persona": True,
            "created_at": _now(),
            "created_by": "bootstrap_iam.py:--create-demo-personas",
            "updated_at": _now(),
        }
        get_container("users").upsert_item(doc)
        created.append({
            "username": username, "password": password,
            "role_key": persona["role_key"], "persona_id": persona["persona_id"],
        })
    return created


# ---------------------------------------------------------------------------
# Ephemeral Azure integration test runs (ephemeral_test_runner.py). One
# document per run, covering its full lifecycle -- see client.py's
# container comment. Never deleted, including after a run's resources are
# cleaned up, so "was this ever actually verified end-to-end against real
# Azure" stays answerable from history.
# ---------------------------------------------------------------------------
def create_ephemeral_test_run(doc: dict) -> dict:
    doc = dict(doc)
    doc.setdefault("created_at", _now())
    doc["updated_at"] = _now()
    return get_container("ephemeral_test_runs").upsert_item(doc)


def get_ephemeral_test_run(run_id: str):
    from azure.cosmos.exceptions import CosmosResourceNotFoundError

    try:
        return get_container("ephemeral_test_runs").read_item(item=run_id, partition_key=run_id)
    except CosmosResourceNotFoundError:
        return None


def update_ephemeral_test_run(doc: dict) -> dict:
    doc["updated_at"] = _now()
    return get_container("ephemeral_test_runs").upsert_item(doc)


def list_ephemeral_test_runs(limit: int = 50) -> list:
    container = get_container("ephemeral_test_runs")
    items = list(
        container.query_items(query="SELECT * FROM c ORDER BY c.created_at DESC", enable_cross_partition_query=True)
    )
    return items[:limit]


# ---------------------------------------------------------------------------
# Job Description Management (jd_management.py) -- the canonical JD record.
# See context-lake/schema/jd.schema.json. Distinct from jd_manager.py's
# list_jds(), which reads jd_folder/*.txt for the existing resume-scoring
# pipeline -- that function is untouched; jd_management.publish_jd() is the
# only bridge between the two (it writes jd_folder/{jd_id}.txt once a JD is
# published, and removes it again on archive).
# ---------------------------------------------------------------------------
def create_jd(jd: dict) -> dict:
    for field in ("jd_id", "title", "status", "created_by"):
        if field not in jd:
            raise ValueError(f"JD dict missing required field '{field}'")
    jd.setdefault("id", jd["jd_id"])
    jd.setdefault("created_at", _now())
    return get_container("jds").upsert_item(jd)


def update_jd(jd: dict) -> dict:
    if "jd_id" not in jd:
        raise ValueError("JD dict missing required field 'jd_id'")
    jd.setdefault("id", jd["jd_id"])
    jd["updated_at"] = _now()
    return get_container("jds").upsert_item(jd)


def get_jd(jd_id: str):
    """Returns None only when the JD genuinely doesn't exist -- connection/
    config errors propagate, same convention as get_candidate()."""
    from azure.cosmos.exceptions import CosmosResourceNotFoundError

    try:
        return get_container("jds").read_item(item=jd_id, partition_key=jd_id)
    except CosmosResourceNotFoundError:
        return None


def list_jd_records(status: str = None) -> list:
    """Lists JDs from the canonical Cosmos record -- NOT jd_manager.py's
    file-based list_jds() (kept separate name deliberately to avoid a
    same-name-different-behavior collision with that existing function).
    ?status= filters to one lifecycle state; omitted returns every JD
    regardless of status (the management page's own view)."""
    container = get_container("jds")
    if status:
        query = "SELECT * FROM c WHERE c.status = @status ORDER BY c.created_at DESC"
        params = [{"name": "@status", "value": status}]
    else:
        query = "SELECT * FROM c ORDER BY c.created_at DESC"
        params = []
    return list(container.query_items(query=query, parameters=params, enable_cross_partition_query=True))


def append_jd_audit_log(entry: dict) -> dict:
    """Business audit trail for JD lifecycle events -- distinct from
    ai_trace_spans (performance telemetry). Mirrors append_audit_log()'s
    shape exactly, keyed by jd_id instead of candidate_id."""
    for field in ("jd_id", "action"):
        if field not in entry:
            raise ValueError(f"JD audit log entry missing required field '{field}'")
    entry.setdefault("entry_id", str(uuid.uuid4()))
    entry.setdefault("id", entry["entry_id"])
    entry.setdefault("logged_at", _now())
    return get_container("jd_audit_log").upsert_item(entry)


def list_jd_audit_log(jd_id: str) -> list:
    container = get_container("jd_audit_log")
    return list(
        container.query_items(
            query="SELECT * FROM c WHERE c.jd_id = @jid ORDER BY c.logged_at ASC",
            parameters=[{"name": "@jid", "value": jd_id}],
            partition_key=jd_id,
        )
    )


# ---------------------------------------------------------------------------
# Unified Observability: snapshot persistence (observability_snapshots.py).
# A snapshot is the one durable record a dashboard reads from by default;
# observability_refresh_results is the per-source breakdown of how that
# snapshot was collected (or why a source failed/was skipped), so a failed
# refresh never erases a prior successful snapshot's data.
# ---------------------------------------------------------------------------
def save_observability_snapshot(snapshot: dict) -> dict:
    for field in ("id", "refresh_run_id", "collected_at", "sources"):
        if field not in snapshot:
            raise ValueError(f"Observability snapshot missing required field '{field}'")
    return get_container("observability_snapshots").upsert_item(snapshot)


def get_latest_observability_snapshot():
    container = get_container("observability_snapshots")
    items = list(container.query_items(
        query="SELECT * FROM c ORDER BY c.collected_at DESC OFFSET 0 LIMIT 1",
        enable_cross_partition_query=True,
    ))
    return items[0] if items else None


def append_observability_refresh_result(result: dict) -> dict:
    for field in ("refresh_run_id", "source_id", "status"):
        if field not in result:
            raise ValueError(f"Refresh result missing required field '{field}'")
    result.setdefault("id", f"{result['refresh_run_id']}:{result['source_id']}")
    return get_container("observability_refresh_results").upsert_item(result)


def list_observability_refresh_results(refresh_run_id: str) -> list:
    container = get_container("observability_refresh_results")
    return list(container.query_items(
        query="SELECT * FROM c WHERE c.refresh_run_id = @rid ORDER BY c.source_id ASC",
        parameters=[{"name": "@rid", "value": refresh_run_id}],
        partition_key=refresh_run_id,
    ))


def list_observability_refresh_history(limit: int = 20) -> list:
    """One row per past refresh run, rolled up from observability_snapshots
    (each snapshot document IS one refresh run's summary) -- newest first."""
    container = get_container("observability_snapshots")
    items = list(container.query_items(
        query="SELECT TOP @lim c.id, c.refresh_run_id, c.collected_at, c.started_at, c.completed_at, "
              "c.triggered_by, c.succeeded_count, c.failed_count, c.skipped_count FROM c ORDER BY c.collected_at DESC",
        parameters=[{"name": "@lim", "value": limit}],
        enable_cross_partition_query=True,
    ))
    return items


# ---------------------------------------------------------------------------
# F-03 (candidate feedback, minimal slice): one feedback document per
# candidate_id, created only when a real final hiring decision has been
# recorded (see candidate_feedback.is_eligible_for_feedback_request()).
# Deliberately a SEPARATE container from candidates/scoring_history --
# see candidate_feedback.py's module docstring on why.
# ---------------------------------------------------------------------------
def get_feedback_request(candidate_id: str):
    from azure.cosmos.exceptions import CosmosResourceNotFoundError

    try:
        return get_container("candidate_feedback").read_item(item=candidate_id, partition_key=candidate_id)
    except CosmosResourceNotFoundError:
        return None


def create_feedback_request(candidate_id: str, reason: str, jd_id: str = None) -> dict:
    """Idempotent: if a feedback request already exists for this
    candidate (whatever its status), returns the EXISTING document
    unchanged rather than overwriting it -- this is the real duplicate-
    request-prevention mechanism, not just a convention."""
    existing = get_feedback_request(candidate_id)
    if existing is not None:
        return existing
    doc = {
        "id": candidate_id, "candidate_id": candidate_id, "jd_id": jd_id,
        "status": "requested", "requested_at": _now(), "requested_reason": reason,
        "submitted_at": None, "ratings": None, "comment": None,
    }
    return get_container("candidate_feedback").upsert_item(doc)


def submit_feedback(candidate_id: str, ratings: dict, comment: str) -> dict:
    """Raises ValueError if no request exists, or it was already
    submitted (one-way transition -- a candidate cannot edit or resubmit
    feedback once given, same "immutable once recorded" discipline as
    this repo's audit_log)."""
    doc = get_feedback_request(candidate_id)
    if doc is None:
        raise ValueError(f"No feedback request exists for candidate '{candidate_id}'.")
    if doc["status"] == "submitted":
        raise ValueError(f"Feedback for candidate '{candidate_id}' was already submitted.")
    doc["status"] = "submitted"
    doc["submitted_at"] = _now()
    doc["ratings"] = ratings
    doc["comment"] = comment
    return get_container("candidate_feedback").upsert_item(doc)


def get_model_routing_config():
    from azure.cosmos.exceptions import CosmosResourceNotFoundError

    try:
        return get_container("model_routing_config").read_item(item="active", partition_key="active")
    except CosmosResourceNotFoundError:
        return None


def save_model_routing_config(routes: dict, actor: str) -> dict:
    """Whole-replace, not a merge -- same semantics as set_pipeline_config().
    The caller (api_blueprint.py) validates `routes` against
    model_routing.ROUTABLE_LABELS before calling this."""
    doc = {"id": "active", "routes": routes, "updated_by": actor, "updated_at": _now()}
    saved = get_container("model_routing_config").upsert_item(doc)
    get_container("model_routing_config_history").upsert_item({
        "id": str(uuid.uuid4()), "actor": actor, "routes": routes, "recorded_at": _now(),
    })
    return saved


def list_model_routing_config_history(limit: int = 50) -> list:
    container = get_container("model_routing_config_history")
    items = list(container.query_items(query="SELECT * FROM c ORDER BY c.recorded_at DESC", enable_cross_partition_query=True))
    return items[:limit]


def list_feedback(jd_id: str = None) -> list:
    container = get_container("candidate_feedback")
    if jd_id:
        query = "SELECT * FROM c WHERE c.jd_id = @jd ORDER BY c.requested_at DESC"
        params = [{"name": "@jd", "value": jd_id}]
    else:
        query = "SELECT * FROM c ORDER BY c.requested_at DESC"
        params = []
    return list(container.query_items(query=query, parameters=params, enable_cross_partition_query=True))
