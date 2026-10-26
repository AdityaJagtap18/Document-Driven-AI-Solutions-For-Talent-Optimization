"""F-14 (End-to-end audit evidence, traceability and candidate data
erasure) -- minimal slice.

Scope note (read before extending this file): the full F-14 feature
prompt describes end-to-end data lineage and erasure across every store
this platform touches -- primary databases, blob storage, parsed-text
files, vector/search indexes, caches, logs, backups, and connected
external services. This module is deliberately NOT that. It is the
smallest real, defensible slice, matching the prior roadmap's own
recommendation: a Cosmos-scoped erasure workflow, with every store this
session has NOT covered stated explicitly as a known, documented gap --
never silently omitted, never claimed as "fully erased" when it isn't.

REAL DATA INVENTORY THIS MODULE IS GROUNDED IN (verified by reading
context-lake/client.py's COSMOS_CONTAINERS and context-lake/
repositories.py directly, not assumed):

  WILL ERASE (real delete, partition key is the candidate_id, or --
  for comms_delivery_log -- correlation_id, which the real
  record_comms_delivery() docstring confirms IS the candidate_id):
    - candidates          (the primary record)
    - interview_transcripts
    - scoring_history
    - comms_delivery_log

  RETAINED, WITH A STATED REASON (never silently skipped):
    - audit_log -- immutable audit evidence. Deleting a candidate's
      audit trail would destroy the evidence this very feature (F-14)
      is partly meant to preserve ("protect audit records against
      unauthorized alteration"). Retained under a documented exception,
      the same distinction the feature prompt itself asks for
      ("retention required by law or another valid obligation").

  FOUND, REPORTED, BUT NOT AUTOMATICALLY MODIFIED (a real, identified
  gap, not a silent one):
    - bulk_decisions -- a batch's `candidate_ids` array is shared with
      OTHER real candidates in the same proposal; deleting the whole
      document would erase people who were never asked to be erased.
      Safe partial redaction isn't implemented in this minimal slice.

  OUT OF SCOPE, NOT TOUCHED BY THIS MODULE AT ALL (stated honestly,
  not fabricated as erased):
    - Raw resume PDFs in Blob Storage (source_blob_path on the
      candidate record points to it, but this module never calls the
      Blob Storage SDK).
    - The real semantic-library index (semantic-library/index/
      resume-chunks.json) -- contains real chunk text and embeddings
      keyed by resume_name, a real, substantial, un-erased PII surface.
    - Legacy CSV files (resume_summary.csv, candidate_ranking_metrics.
      csv) and local JSON artifact folders (InterviewData/,
      OutputResume_Folder/, ParsedReadyForQuestions/).
    - Local JSONL observability logs (logs/llm_observability.jsonl,
      logs/ai_traces.jsonl) -- confirmed in this session's own prior
      work that these never log raw candidate text (see observability.
      py's record_llm_call()/record_ai_trace() docstrings), so the real
      residual-PII risk here is low, but they are not scanned or
      touched by this module either.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "context-lake"))

import repositories  # noqa: E402

ERASE_CONTAINERS = ("candidates", "interview_transcripts", "scoring_history", "comms_delivery_log")

RETAINED_CONTAINERS = {
    "audit_log": "Immutable audit evidence trail, retained for compliance/investigation purposes per this "
                 "feature's own retention exception -- not deleted. The candidate_id remains associated with "
                 "historical action records (who did what, when), which this module treats as a legitimate, "
                 "documented retention obligation, not an oversight.",
}

OUT_OF_SCOPE_STORES = [
    {"store": "Blob Storage (raw resume PDF)", "reason": "This module never calls the Blob Storage SDK -- the file at the candidate's source_blob_path is not deleted."},
    {"store": "semantic-library/index/resume-chunks.json", "reason": "Real chunk text and embeddings for this candidate's resume remain indexed -- a real, substantial, un-erased PII surface, not covered by this minimal slice."},
    {"store": "Legacy CSV files (resume_summary.csv, candidate_ranking_metrics.csv)", "reason": "Not scanned or modified by this module."},
    {"store": "Local JSON artifact folders (InterviewData/, OutputResume_Folder/, ParsedReadyForQuestions/)", "reason": "Not scanned or modified by this module."},
    {"store": "Local JSONL observability logs (logs/llm_observability.jsonl, logs/ai_traces.jsonl)", "reason": "Confirmed in this session's prior work that these don't log raw candidate text, but they are not scanned or touched by this module either."},
]


def _now():
    return datetime.now(timezone.utc).isoformat()


def locate_candidate_data(candidate_id: str) -> dict:
    """Read-only discovery pass -- erases nothing. Reports exactly what
    real data exists for this candidate across every container this
    module knows about, so an authorized caller can review before
    triggering actual erasure. Never fabricates a count; every number
    here is a real query result."""
    will_erase = {}
    for container_name in ERASE_CONTAINERS:
        if container_name == "candidates":
            will_erase[container_name] = 1 if repositories.get_candidate(candidate_id) is not None else 0
        elif container_name == "interview_transcripts":
            will_erase[container_name] = len(repositories.list_interview_transcripts(candidate_id))
        elif container_name == "scoring_history":
            will_erase[container_name] = len(repositories.list_scoring_history(candidate_id))
        elif container_name == "comms_delivery_log":
            will_erase[container_name] = len(repositories.list_comms_deliveries(candidate_id, limit=10000))

    retained = {name: {"count": len(repositories.list_audit_log(candidate_id)), "reason": reason} for name, reason in RETAINED_CONTAINERS.items()}

    entangled = repositories.find_bulk_decisions_referencing_candidate(candidate_id)

    return {
        "candidate_id": candidate_id,
        "checked_at": _now(),
        "will_erase": will_erase,
        "retained": retained,
        "found_entangled_not_modified": {"bulk_decisions": entangled} if entangled else {},
        "out_of_scope_stores": OUT_OF_SCOPE_STORES,
    }


def erase_candidate(candidate_id: str) -> dict:
    """Runs the real Cosmos-scoped erasure, then RE-QUERIES every erased
    container to verify 0 real records remain -- never trusts a delete
    call's own success alone. Returns a verifiable erasure report:
    exactly what was erased, what was retained and why, what was found
    but not touched, and what's explicitly out of scope. Safe to call on
    a candidate_id with no data at all (every count is simply 0 -- not
    an error, per repositories.delete_candidate()'s own "no-op success"
    convention)."""
    started_at = _now()

    erased = {
        "candidates": 1 if repositories.delete_candidate(candidate_id) else 0,
        "interview_transcripts": repositories.delete_interview_transcripts_for_candidate(candidate_id),
        "scoring_history": repositories.delete_scoring_history_for_candidate(candidate_id),
        "comms_delivery_log": repositories.delete_comms_deliveries_for_candidate(candidate_id),
    }

    # Real post-deletion verification -- re-queries, doesn't assume.
    verification = {
        "candidates": "0 remaining" if repositories.get_candidate(candidate_id) is None else "STILL PRESENT -- erasure incomplete",
        "interview_transcripts": f"{len(repositories.list_interview_transcripts(candidate_id))} remaining",
        "scoring_history": f"{len(repositories.list_scoring_history(candidate_id))} remaining",
        "comms_delivery_log": f"{len(repositories.list_comms_deliveries(candidate_id, limit=10000))} remaining",
    }

    retained = {name: {"count": len(repositories.list_audit_log(candidate_id)), "reason": reason} for name, reason in RETAINED_CONTAINERS.items()}
    entangled = repositories.find_bulk_decisions_referencing_candidate(candidate_id)

    return {
        "candidate_id": candidate_id,
        "started_at": started_at,
        "completed_at": _now(),
        "erased": erased,
        "verification": verification,
        "retained": retained,
        "found_entangled_not_modified": {"bulk_decisions": entangled} if entangled else {},
        "out_of_scope_stores": OUT_OF_SCOPE_STORES,
    }
