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
    try:
        return get_container("candidates").read_item(item=candidate_id, partition_key=candidate_id)
    except Exception:
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
