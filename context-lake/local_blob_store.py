"""Local-disk-primary blob storage, same architecture as
local_json_store.py but for files instead of JSON documents.

This app used to read AND write resume files straight through Azure Blob
Storage (client.py's get_blob_container()) -- so an Azure outage didn't
just block new writes, it blocked resume uploads entirely. The
architecture here flips who's primary: ReplicatedBlobContainer below
writes to local disk (data/local_blobs/<container>/<path>) FIRST and
synchronously -- that's what the caller's success/failure and every read
reflect -- then mirrors the write to the real Azure container as a
best-effort BACKUP replica, time-boxed so a slow/unreachable Azure
Storage account can never block or fail an upload that already
succeeded locally. Reads never touch Azure at all.

Only the tiny slice of the azure-storage-blob SDK surface this repo's
resume-file code paths actually use is implemented here -- upload_blob /
get_blob_client / download_blob / list_blobs / exists -- so
blob_store.py, hostinger-deploy/resume_blob_poller.py, and
azure-functions/function_app.py's trigger don't need to change at all.
"""

import os
import threading
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_STORE_DIR = _REPO_ROOT / "data" / "local_blobs"

_BACKUP_WRITE_TIMEOUT_SECONDS = 10


def _run_in_background_with_timeout(fn, timeout_seconds: float):
    """Same shape as local_json_store._run_in_background_with_timeout --
    runs fn() on a daemon thread, waits up to timeout_seconds, and
    otherwise gives up (fn keeps running in the background, its result
    discarded). Used for the Azure backup write so an unreachable or slow
    Azure Storage account can never block a local-primary upload."""
    result_box = {}

    def _run():
        try:
            result_box["value"] = fn()
        except Exception as e:
            result_box["error"] = e

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=timeout_seconds)
    if thread.is_alive():
        return False, None
    if "error" in result_box:
        return False, result_box["error"]
    return True, result_box.get("value")


class _LocalBlobClient:
    """Mimics azure.storage.blob.BlobClient's exists()/download_blob()."""

    def __init__(self, path: Path):
        self._path = path

    def exists(self) -> bool:
        return self._path.is_file()

    def download_blob(self):
        return _LocalDownloadStream(self._path)


class _LocalDownloadStream:
    """Mimics the StorageStreamDownloader readall() returned by
    azure-storage-blob's download_blob()."""

    def __init__(self, path: Path):
        self._path = path

    def readall(self) -> bytes:
        return self._path.read_bytes()


class _BlobProperties:
    """Mimics the BlobProperties entries azure-storage-blob's
    list_blobs() yields -- callers here only ever read `.name`."""

    def __init__(self, name: str):
        self.name = name


class LocalBlobContainer:
    """Local-disk-only container (no Azure backup). Used directly as the
    local half of ReplicatedBlobContainer, and usable standalone."""

    def __init__(self, container_name: str):
        self.container_name = container_name
        self.root = _STORE_DIR / container_name
        self.root.mkdir(parents=True, exist_ok=True)

    def upload_blob(self, name: str, data, overwrite: bool = True) -> None:
        dest = self.root / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not overwrite and dest.exists():
            raise FileExistsError(f"Blob '{name}' already exists in '{self.container_name}' and overwrite=False.")
        payload = data.read() if hasattr(data, "read") else data
        dest.write_bytes(payload)

    def get_blob_client(self, name: str) -> _LocalBlobClient:
        return _LocalBlobClient(self.root / name)

    def download_blob(self, name: str):
        return self.get_blob_client(name).download_blob()

    def list_blobs(self):
        if not self.root.is_dir():
            return []
        return [
            _BlobProperties(str(path.relative_to(self.root)).replace(os.sep, "/"))
            for path in sorted(self.root.rglob("*")) if path.is_file()
        ]

    def exists(self) -> bool:
        return True  # the local directory is created in __init__, always usable


class ReplicatedBlobContainer:
    """Public container object for `name` in client.BLOB_CONTAINERS --
    local disk is PRIMARY (every upload/download/list goes through
    LocalBlobContainer and succeeds regardless of Azure), real Azure Blob
    Storage (if BLOB_CONNECTION_STRING is configured) is a best-effort
    BACKUP mirror of every upload, fired on a daemon thread and never
    awaited past _BACKUP_WRITE_TIMEOUT_SECONDS -- a Azure Storage failure
    of any kind (down, unreachable, not configured, slow) is swallowed
    and never surfaces to the caller, mirroring
    local_json_store.ReplicatedContainer's design for Cosmos."""

    def __init__(self, container_name: str):
        self.container_name = container_name
        self.local = LocalBlobContainer(container_name)

    def _raw_azure_container(self):
        """The actual azure-storage-blob ContainerClient -- bypasses
        client.get_blob_container() (which now returns this class) to
        avoid infinite recursion."""
        import client as context_lake_client
        return context_lake_client._raw_blob_container(self.container_name)

    def _backup_write_async(self, fn):
        threading.Thread(
            target=lambda: _run_in_background_with_timeout(fn, _BACKUP_WRITE_TIMEOUT_SECONDS), daemon=True,
        ).start()

    def upload_blob(self, name: str, data, overwrite: bool = True) -> None:
        payload = data.read() if hasattr(data, "read") else data
        self.local.upload_blob(name, payload, overwrite=overwrite)
        self._backup_write_async(lambda: self._raw_azure_container().upload_blob(name=name, data=payload, overwrite=overwrite))

    def get_blob_client(self, name: str) -> _LocalBlobClient:
        return self.local.get_blob_client(name)

    def download_blob(self, name: str):
        return self.local.download_blob(name)

    def list_blobs(self):
        return self.local.list_blobs()

    def exists(self) -> bool:
        return True
