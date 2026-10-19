import os
import sys
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from secrets_loader import get_secret  # noqa: E402

COSMOS_CONTAINERS = {
    "candidates": "/candidate_id",
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
