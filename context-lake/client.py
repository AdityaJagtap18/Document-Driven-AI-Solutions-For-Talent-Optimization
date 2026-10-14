import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()

COSMOS_CONTAINERS = {
    "candidates": "/candidate_id",
    "interview_transcripts": "/candidate_id",
    "scoring_history": "/candidate_id",
    "audit_log": "/candidate_id",
}

BLOB_CONTAINERS = ["raw-resumes", "parsed-resumes"]


def _require_env(names):
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise RuntimeError(
            f"Missing required environment variable(s): {', '.join(missing)}. "
            "Add them to your .env file (see .env.example) once the Azure Cosmos DB "
            "and/or Storage resources are provisioned."
        )


@lru_cache(maxsize=1)
def get_cosmos_database():
    """Returns the Cosmos DB database client, creating the database if it doesn't exist.
    Requires COSMOS_ENDPOINT, COSMOS_KEY, COSMOS_DATABASE in the environment."""
    _require_env(["COSMOS_ENDPOINT", "COSMOS_KEY", "COSMOS_DATABASE"])
    from azure.cosmos import CosmosClient

    client = CosmosClient(os.environ["COSMOS_ENDPOINT"], credential=os.environ["COSMOS_KEY"])
    return client.create_database_if_not_exists(id=os.environ["COSMOS_DATABASE"])


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
    """Returns the Blob service client. Requires BLOB_CONNECTION_STRING in the environment."""
    _require_env(["BLOB_CONNECTION_STRING"])
    from azure.storage.blob import BlobServiceClient

    return BlobServiceClient.from_connection_string(os.environ["BLOB_CONNECTION_STRING"])


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
