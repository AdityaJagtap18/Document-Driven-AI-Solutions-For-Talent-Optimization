"""Upload/download helpers for the two Blob Storage containers (raw-resumes,
parsed-resumes). Flat import, same convention as repositories.py."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from client import get_blob_container  # noqa: E402


def upload_resume_blob(container_name: str, blob_path: str, file_path: str) -> str:
    """Uploads a local file to 'raw-resumes' or 'parsed-resumes'. Returns blob_path."""
    container = get_blob_container(container_name)
    with open(file_path, "rb") as f:
        container.upload_blob(name=blob_path, data=f, overwrite=True)
    return blob_path


def upload_resume_bytes(container_name: str, blob_path: str, data: bytes) -> str:
    """Uploads raw bytes (e.g. a file from an HTTP request body, no local
    file to read from) to 'raw-resumes' or 'parsed-resumes'. Returns
    blob_path. Used by api_blueprint.py's application-submission endpoint."""
    container = get_blob_container(container_name)
    container.upload_blob(name=blob_path, data=data, overwrite=True)
    return blob_path


def download_resume_blob(container_name: str, blob_path: str, dest_path: str) -> str:
    """Downloads a blob to a local file. Returns dest_path."""
    container = get_blob_container(container_name)
    blob = container.get_blob_client(blob_path)
    with open(dest_path, "wb") as f:
        f.write(blob.download_blob().readall())
    return dest_path


def blob_exists(container_name: str, blob_path: str) -> bool:
    container = get_blob_container(container_name)
    return container.get_blob_client(blob_path).exists()
