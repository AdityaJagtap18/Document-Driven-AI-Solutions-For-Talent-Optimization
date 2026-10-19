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
