import os
import sys
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from secrets_loader import get_secret  # noqa: E402

COSMOS_CONTAINERS = {
    "candidates": "/candidate_id",
    # Unified Observability: one document per completed refresh run
    # (observability_snapshots.py) -- the full collected snapshot across
    # every real source (Azure Monitor, Azure Readiness, local AI traces,
    # resume-processing/comms reports), persisted so the dashboard reads
    # from here by default instead of re-querying Azure on every page
    # load. Partition key /id since only the single latest snapshot is
    # normally read, plus history is listed as a whole set.
    "observability_snapshots": "/id",
    # One row per (refresh_run, source) collection attempt -- the
    # persistent refresh-history table (status, duration, error) a
    # snapshot document's own per-source results are rolled up from.
    # Partition key /refresh_run_id so "all source results for this one
    # refresh" is a cheap single-partition query.
    "observability_refresh_results": "/refresh_run_id",
    # Job Description Management: the canonical JD record (status, fields,
    # approval metadata) -- see jd_management.py and
    # context-lake/schema/jd.schema.json. Separate from (and the source of
    # truth for) jd_folder/*.txt, which jd_manager.py's existing resume-
    # scoring pipeline reads -- only PUBLISHED JDs are synced there (see
    # jd_management.publish_jd()), so draft/pending JDs never affect
    # candidate matching. Partition key /id since JDs are listed/filtered
    # by status as a whole set, not scoped to any other entity.
    "jds": "/id",
    # Business audit trail for JD lifecycle events (create/update/submit/
    # approve/reject/publish/archive) -- distinct from ai_trace_spans
    # below (that's AI-pipeline performance telemetry; this is "who did
    # what to this JD and when"), mirroring audit_log's candidate-keyed
    # pattern but keyed by /jd_id since there's no candidate_id on a JD
    # lifecycle event.
    "jd_audit_log": "/jd_id",
    "interview_transcripts": "/candidate_id",
    "scoring_history": "/candidate_id",
    "audit_log": "/candidate_id",
    # Single-document container: one recruiter-configurable ordered list of
    # hiring-process stage names (Shortlisted, Aptitude Exam, Technical
    # Exam, ... Selected/Rejected), shared across all candidates/JDs for
    # now -- see repositories.get_pipeline_config(). Partition key is just
    # /id since there's only ever the one "default" document.
    "pipeline_config": "/id",
    # Bulk screening proposals (recruiter proposes accept/reject for a
    # batch of candidates; nothing happens to any candidate until an
    # admin approves it) -- see repositories.create_bulk_decision().
    # Partitioned by /id since these are read/listed as a whole set
    # (pending-approvals queue), not scoped to one candidate.
    "bulk_decisions": "/id",
    # Real user accounts (replaces the fully client-side demo login in
    # Login.jsx). Role assignments are embedded as a list of role_keys on
    # the user document itself, not a separate join-table container --
    # idiomatic for Cosmos/NoSQL and this app's scale (a handful of roles
    # per user). Partition key /id since users are listed/looked-up by
    # username, not scoped to any other entity.
    "users": "/id",
    # Role definitions (role_key, display_name, permission_ids list).
    # Permission *definitions* themselves are a static, code-defined
    # catalog (auth.PERMISSIONS) -- tied to actual code paths, not
    # admin-editable data -- but which permissions each role grants is
    # stored here so Admin can edit non-system (custom) roles' grants.
    "roles": "/id",
    # Registered application pages (auth.PAGES is the code-defined catalog
    # -- this app's "route registry", see that list's docstring; this
    # container is what repositories.sync_pages() reconciles it against).
    # Partition key /id (= page_id) since pages are listed/searched as a
    # whole set in the Admin Panel's Page Access Management table.
    "pages": "/id",
    # Per-(page, role) access decisions -- deny-by-default; a page with no
    # policy document for a given role is NOT accessible to that role. id
    # is "{page_id}:{role_key}" so each pair has exactly one document
    # (upsert-to-update, no duplicate-policy risk). Partition key /id for
    # the same "read as a whole set" reason as pages above.
    "page_access_policies": "/id",
    # AI Observability Center: one document per trace SPAN (one pipeline
    # stage -- document extraction, chunking, embedding generation, an
    # LLM call, etc.), written by observability.record_ai_trace() on a
    # best-effort basis (see that function's docstring -- fails soft,
    # the always-written local logs/ai_traces.jsonl is unaffected if
    # Cosmos isn't configured). Partition key /trace_id so every span of
    # one end-to-end workflow run is in the same partition -- the
    # trace explorer's "get all spans for this trace" read is then a
    # cheap single-partition query, not a cross-partition fan-out.
    "ai_trace_spans": "/trace_id",
    # Azure Service Hub: registered integration instances (one per
    # adapter_type the app actually uses -- see azure_integrations.py's
    # ADAPTER_TYPES). Partition key /id since instances are listed as a
    # whole small set, not scoped to any other entity.
    "integration_instances": "/id",
    # Health-check HISTORY (not just the latest result) -- one document
    # per check run. Partition key /integration_id so "this integration's
    # check history" is a cheap single-partition query.
    "integration_health_checks": "/integration_id",
    # Configuration CHANGE history -- distinct from health-check history
    # above (that's "did a live connection succeed"; this is "what was
    # the config and who changed it"). One document per register/
    # config-update/enable/disable/remove, with a safe (never secret-
    # bearing) summary of what changed -- see repositories.py's
    # record_integration_config_change(). Partition key /integration_id
    # for the same single-partition-read reason as health checks.
    "integration_config_history": "/integration_id",
    # RAG Configuration Console: ONE singleton document per lifecycle
    # state ("active", and "draft" once an admin starts editing) for the
    # resume-matching RAG pipeline's tunable parameters (chunking.
    # max_chars, retrieval.top_k/similarity_threshold, generation
    # model/temperature/max_tokens) -- see rag_config.py. Partition key
    # /id since there are only ever these 1-2 documents.
    "rag_configurations": "/id",
    # Configuration change history for the above -- same "distinct from
    # a connectivity/test-run log" pattern as integration_config_history.
    # Partition key /id (there's one RAG pipeline, so no natural
    # per-entity partition -- read as a whole small set, newest first).
    "rag_config_history": "/id",
    # Versioned prompt templates for the two real, hardcoded-until-now
    # prompts in ResumeParser.py (resume_chunk_summarization,
    # resume_merge_structured_extraction) -- see rag_config.py's
    # docstring for why these seed from the existing prompts/*.md files
    # rather than inventing new text. Partition key /prompt_id so "every
    # version of this prompt" is a cheap single-partition query.
    "rag_prompt_versions": "/prompt_id",
    # Communications Configuration Console -- see comms_config.py's module
    # docstring. "comms_configurations" holds the email/WhatsApp/delivery-
    # policy singleton ("active"/"draft"); "interview_bot_configurations"
    # is a SEPARATE singleton for the voice interview bot, kept apart per
    # the spec's explicit "keep bot config separate from communication
    # configuration." Both /id partitioned (1-2 docs each, read as a set).
    "comms_configurations": "/id",
    "comms_config_history": "/id",
    "interview_bot_configurations": "/id",
    # Versioned message templates -- partitioned by /template_id so "every
    # version of this template" is a cheap single-partition query, same
    # pattern as rag_prompt_versions.
    "comms_template_versions": "/template_id",
    # Real delivery log -- one document per actual send attempt (not a
    # connectivity check). Partition key /correlation_id (the candidate_id)
    # so "this candidate's delivery history" is a cheap single-partition
    # query, and so duplicate-send protection can query it directly.
    "comms_delivery_log": "/correlation_id",
    # Ephemeral Azure integration test-run lifecycle records (Mode C of
    # the Azure Readiness dashboard's provision-test-destroy workflow --
    # see ephemeral_test_runner.py). One document per run, covering its
    # full PLANNED -> ... -> CLEANUP_VERIFIED/CLEANUP_FAILED lifecycle --
    # never deleted even after cleanup, so a past run's outcome (and
    # whether cleanup actually succeeded) stays auditable. Partition key
    # /id since runs are listed/searched as a whole small set.
    "ephemeral_test_runs": "/id",
    # F-02 (Tokenomics calculator, minimal slice): the real budget/alert
    # configuration -- same Draft -> Activate -> History lifecycle already
    # proven for rag_configurations/comms_configurations, not a new
    # pattern. Partition key /id since there's only ever "active"/"draft"
    # documents, same as rag_configurations.
    "tokenomics_config": "/id",
    "tokenomics_config_history": "/id",
    # F-03 (candidate feedback, minimal slice): one feedback-request/
    # submission document per candidate (id = candidate_id), kept
    # entirely separate from "candidates"/"scoring_history" so
    # experience feedback can never influence hiring evaluation scores
    # -- see candidate_feedback.py's module docstring. Partition key
    # /candidate_id matches the other candidate-keyed containers
    # (interview_transcripts, scoring_history, audit_log).
    "candidate_feedback": "/candidate_id",
    # F-17 (model routing, minimal slice): a single "active" document
    # mapping label -> model override for the 6 real LLM call sites that
    # have no admin-configurable model today -- see model_routing.py's
    # module docstring. No Draft/Activate staging (unlike rag_configurations)
    # -- a routing-rule change is low-risk and instantly reversible by
    # resaving, not worth the extra staging ceremony. Partition key /id
    # since there's only ever the one "active" document.
    "model_routing_config": "/id",
    "model_routing_config_history": "/id",
}

BLOB_CONTAINERS = ["raw-resumes", "parsed-resumes"]


def _require_secrets(names):
    """Looks each name up via get_secret() (Key Vault first, then .env) and
    returns {name: value}. Raises if any are missing from both."""
    values = {n: get_secret(n) for n in names}
    missing = [n for n, v in values.items() if not v]
    if missing:
        raise RuntimeError(
            f"Missing required secret(s): {', '.join(missing)}. "
            "Add them to your .env file (see .env.example) or Key Vault, once the Azure "
            "Cosmos DB and/or Storage resources are provisioned."
        )
    return values


@lru_cache(maxsize=1)
def get_cosmos_database():
    """Returns the Cosmos DB database client, creating the database if it doesn't exist.
    Requires COSMOS_ENDPOINT, COSMOS_KEY, COSMOS_DATABASE (env or Key Vault)."""
    values = _require_secrets(["COSMOS_ENDPOINT", "COSMOS_KEY", "COSMOS_DATABASE"])
    from azure.cosmos import CosmosClient

    client = CosmosClient(values["COSMOS_ENDPOINT"], credential=values["COSMOS_KEY"])
    return client.create_database_if_not_exists(id=values["COSMOS_DATABASE"])


def get_container(name: str):
    """Returns a Cosmos DB container client, creating the container if it doesn't exist.
    `name` must be one of COSMOS_CONTAINERS (candidates, interview_transcripts,
    scoring_history, audit_log) — see ../context-lake/README.md for what each holds."""
    if name not in COSMOS_CONTAINERS:
        raise ValueError(f"Unknown container '{name}'. Known containers: {list(COSMOS_CONTAINERS)}")
    from azure.cosmos import PartitionKey

    db = get_cosmos_database()
    return db.create_container_if_not_exists(
        id=name, partition_key=PartitionKey(path=COSMOS_CONTAINERS[name])
    )


@lru_cache(maxsize=1)
def get_blob_service_client():
    """Returns the Blob service client. Requires BLOB_CONNECTION_STRING (env or Key Vault)."""
    values = _require_secrets(["BLOB_CONNECTION_STRING"])
    from azure.storage.blob import BlobServiceClient

    return BlobServiceClient.from_connection_string(values["BLOB_CONNECTION_STRING"])


def get_blob_container(name: str):
    """Returns a Blob container client, creating the container if it doesn't exist.
    `name` must be one of BLOB_CONTAINERS (raw-resumes, parsed-resumes)."""
    if name not in BLOB_CONTAINERS:
        raise ValueError(f"Unknown blob container '{name}'. Known containers: {BLOB_CONTAINERS}")
    svc = get_blob_service_client()
    container = svc.get_container_client(name)
    if not container.exists():
        container.create_container()
    return container
