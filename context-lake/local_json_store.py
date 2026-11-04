"""Local-disk-primary persistence for every Cosmos-backed container.

This app used to read AND write every container straight through Azure
Cosmos DB (client.py's get_container()) -- so an Azure outage didn't just
block new writes, it blocked login, the candidate pipeline, everything.

The architecture here flips who's primary: ReplicatedContainer below
serves every read from a local JSON file on disk (data/local_store/
<container>.json at the repo root) and writes there FIRST and
synchronously; Cosmos is then written to as a best-effort BACKUP replica,
time-boxed so a slow/unreachable Cosmos can never block or fail a write
that already succeeded locally. Reads never touch Cosmos at all. This
means the app keeps working -- reading its real, current data and
accepting new writes -- through a full Azure/Cosmos outage.

Bootstrapping existing data: the first time a container's local file
doesn't exist yet, ReplicatedContainer makes ONE best-effort, time-boxed
attempt to pull every document Cosmos already has for that container
into the local file (see _seed_from_cosmos_if_empty()) -- so switching to
this architecture doesn't make pre-existing Cosmos data (candidates,
users, ...) vanish, as long as Cosmos is reachable at least once after
this change ships. seed_all_from_cosmos() below does the same for every
container at once, for an explicit one-time migration run.

Only the handful of Cosmos SQL query shapes repositories.py actually
issues (see _run_query) are supported -- this is not a general Cosmos
emulator, just enough of one for this repo's real, fixed set of queries.
"""

import json
import re
import sys
import threading
import uuid
from pathlib import Path
from threading import Lock

_REPO_ROOT = Path(__file__).resolve().parent.parent
_STORE_DIR = _REPO_ROOT / "data" / "local_store"

_BACKUP_WRITE_TIMEOUT_SECONDS = 10
_SEED_READ_TIMEOUT_SECONDS = 10

_locks = {}


def _lock_for(name: str) -> Lock:
    return _locks.setdefault(name, Lock())


def _not_found_error_class():
    """repositories.py's existing callers (e.g. get_integration_instance())
    catch azure.cosmos.exceptions.CosmosResourceNotFoundError specifically --
    importing just that exception class is a static, no-network import (the
    azure-cosmos package is already a dependency), so read_item() below can
    raise the real class and every existing except-clause keeps working
    unmodified, with no live Cosmos connection required to raise it."""
    from azure.cosmos.exceptions import CosmosResourceNotFoundError
    return CosmosResourceNotFoundError


def _run_in_background_with_timeout(fn, timeout_seconds: float):
    """Runs fn() on a daemon thread, waits up to timeout_seconds for it,
    and otherwise gives up and returns (False, None) -- fn keeps running in
    the background and its eventual result (if any) is discarded. Used for
    every Cosmos touch here (backup write, seed read) so an unreachable or
    slow Azure can never block a local-primary operation that has already
    (or will anyway) succeed on disk. Returns (completed, value_or_exception)."""
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


class LocalJSONContainer:
    """Mimics the tiny slice of the azure-cosmos ContainerProxy interface
    this repo's repositories.py actually calls, backed by one JSON file
    per container on local disk. Used directly as the sole backend for
    the Azure report/snapshot containers, and wrapped by
    ReplicatedContainer below as the local-primary half for every other
    (real business data) container."""

    def __init__(self, name: str):
        self.name = name
        self._path = _STORE_DIR / f"{name}.json"

    def exists_on_disk(self) -> bool:
        return self._path.exists()

    def _load(self) -> list:
        if not self._path.exists():
            return []
        with open(self._path, "r", encoding="utf-8") as f:
            raw = f.read().strip()
        if not raw:
            return []
        return json.loads(raw)

    def _save(self, items: list) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(".json.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2, default=str)
        tmp_path.replace(self._path)

    def seed(self, items: list) -> None:
        """Bulk-writes an initial set of documents (from Cosmos) -- only
        ever called when the local file doesn't exist yet, never to
        overwrite real local writes that already happened."""
        with _lock_for(self.name):
            if not self._path.exists():
                self._save(list(items))

    def upsert_item(self, body: dict) -> dict:
        if "id" not in body:
            body = dict(body)
            body["id"] = str(uuid.uuid4())
        with _lock_for(self.name):
            items = self._load()
            items = [i for i in items if i.get("id") != body["id"]]
            items.append(body)
            self._save(items)
        return body

    def read_item(self, item: str, partition_key=None) -> dict:
        for doc in self._load():
            if doc.get("id") == item:
                return doc
        raise _not_found_error_class()(message=f"No item with id '{item}' in '{self.name}'.")

    def delete_item(self, item: str, partition_key=None) -> None:
        with _lock_for(self.name):
            items = [i for i in self._load() if i.get("id") != item]
            self._save(items)

    def query_items(self, query: str, parameters: list = None, partition_key=None,
                     enable_cross_partition_query: bool = None):
        return _run_query(self._load(), query, parameters or [])


class ReplicatedContainer:
    """Local disk is the real, authoritative copy -- every read AND write
    goes here synchronously, so this is what determines whether an
    operation succeeds or what a read returns. Cosmos is written to
    afterwards as a best-effort BACKUP replica only: its outcome is never
    awaited past _BACKUP_WRITE_TIMEOUT_SECONDS, and a Cosmos failure of
    any kind (down, unreachable, not configured, slow) is swallowed and
    never surfaces to the caller -- by design, per the "it won't show if
    Cosmos is down" requirement this was built for. Cosmos is still a
    REAL backup when it IS reachable: every successful local write is
    mirrored there too, so restoring from it is possible if the local
    disk is ever lost."""

    def __init__(self, name: str):
        self.name = name
        self.local = LocalJSONContainer(name)
        self._seeded = False

    def _raw_cosmos_container(self):
        """The actual azure-cosmos ContainerProxy -- bypasses
        client.get_container() (which now returns a ReplicatedContainer,
        i.e. this class) to avoid infinite recursion."""
        import client as context_lake_client
        from azure.cosmos import PartitionKey

        db = context_lake_client.get_cosmos_database()
        return db.create_container_if_not_exists(
            id=self.name, partition_key=PartitionKey(path=context_lake_client.COSMOS_CONTAINERS[self.name])
        )

    def _ensure_seeded(self):
        """One best-effort, time-boxed attempt per process to pull this
        container's existing Cosmos documents down into the (as-yet
        nonexistent) local file, so switching to local-primary doesn't
        make pre-existing Cosmos data disappear. Runs at most once per
        container per process; a local file that already exists (even
        empty, even from a prior real write) is never touched here."""
        if self._seeded or self.local.exists_on_disk():
            self._seeded = True
            return
        self._seeded = True  # never retry mid-process even if this attempt fails

        def _fetch_all():
            container = self._raw_cosmos_container()
            return list(container.query_items(query="SELECT * FROM c", enable_cross_partition_query=True))

        completed, value = _run_in_background_with_timeout(_fetch_all, _SEED_READ_TIMEOUT_SECONDS)
        if completed and isinstance(value, list):
            self.local.seed(value)

    def _backup_write_async(self, fn):
        """Fires a best-effort Cosmos write and does not wait for or
        propagate its outcome at all -- not even up to the timeout, since
        the caller's local write already succeeded and must not be
        delayed by Cosmos's latency on the success path."""
        threading.Thread(target=lambda: _run_in_background_with_timeout(fn, _BACKUP_WRITE_TIMEOUT_SECONDS), daemon=True).start()

    def upsert_item(self, body: dict) -> dict:
        self._ensure_seeded()
        saved = self.local.upsert_item(body)
        self._backup_write_async(lambda: self._raw_cosmos_container().upsert_item(saved))
        return saved

    def read_item(self, item: str, partition_key=None) -> dict:
        self._ensure_seeded()
        return self.local.read_item(item, partition_key)

    def delete_item(self, item: str, partition_key=None) -> None:
        self._ensure_seeded()
        self.local.delete_item(item, partition_key)
        self._backup_write_async(lambda: self._raw_cosmos_container().delete_item(item=item, partition_key=partition_key))

    def query_items(self, query: str, parameters: list = None, partition_key=None,
                     enable_cross_partition_query: bool = None):
        self._ensure_seeded()
        return self.local.query_items(query, parameters, partition_key, enable_cross_partition_query)


def seed_all_from_cosmos(container_names=None) -> dict:
    """Explicit, one-time migration helper -- pulls every document Cosmos
    currently has for each container into its local JSON file. Meant to
    be run once (with real Cosmos secrets available) right after this
    local-primary architecture ships, so local disk starts out with
    today's real data rather than relying on the lazy per-container seed
    in ReplicatedContainer._ensure_seeded() happening to fire before
    anyone writes to a container that hasn't been read yet. Skips any
    container whose local file already exists (never overwrites real
    local data) and any container Cosmos itself can't deliver (reported
    in the returned per-container status, never silently). Safe to run
    more than once -- it is a no-op for anything already seeded."""
    import client as context_lake_client

    names = container_names or list(context_lake_client.COSMOS_CONTAINERS)
    report = {}
    for name in names:
        local = LocalJSONContainer(name)
        if local.exists_on_disk():
            report[name] = "skipped (local file already exists)"
            continue
        replicated = ReplicatedContainer(name)
        replicated._ensure_seeded()
        report[name] = "seeded" if local.exists_on_disk() else "could not reach Cosmos -- starting empty"
    return report


# ---------------------------------------------------------------------------
# A tiny, fixed-shape Cosmos-SQL reader -- only supports the exact clauses
# repositories.py's real queries use across the whole app: an optional
# "WHERE c.<field> = @param" or "WHERE ARRAY_CONTAINS(c.<field>, @param)",
# an optional "TOP @param" or "TOP n", an optional "ORDER BY c.<f1> ASC|DESC
# [, c.<f2> ASC|DESC ...]", and an optional trailing "OFFSET n LIMIT m".
# Projection lists (SELECT c.a, c.b, ... instead of SELECT *) are
# intentionally ignored -- the full document is returned either way, which
# every caller in this repo already tolerates (they only ever read named
# fields off the result, never assert on its total field set).
# ---------------------------------------------------------------------------
def _param_value(name: str, parameters: list):
    for p in parameters:
        if p.get("name") == name:
            return p.get("value")
    raise KeyError(f"Query references undeclared parameter '{name}'.")


def _run_query(items: list, query: str, parameters: list) -> list:
    array_contains_match = re.search(r"WHERE\s+ARRAY_CONTAINS\(c\.(\w+)\s*,\s*(@\w+)\)", query, re.IGNORECASE)
    if array_contains_match:
        field, param_name = array_contains_match.group(1), array_contains_match.group(2)
        value = _param_value(param_name, parameters)
        items = [i for i in items if isinstance(i.get(field), list) and value in i[field]]
    else:
        where_match = re.search(r"WHERE\s+c\.(\w+)\s*=\s*(@\w+)", query, re.IGNORECASE)
        if where_match:
            field, param_name = where_match.group(1), where_match.group(2)
            value = _param_value(param_name, parameters)
            items = [i for i in items if i.get(field) == value]

    order_match = re.search(r"ORDER BY\s+(c\.\w+\s+(?:ASC|DESC)(?:\s*,\s*c\.\w+\s+(?:ASC|DESC))*)", query, re.IGNORECASE)
    if order_match:
        order_fields = re.findall(r"c\.(\w+)\s+(ASC|DESC)", order_match.group(1), re.IGNORECASE)
        for field, direction in reversed(order_fields):  # stable sort, last ORDER BY field is the primary sort key applied last
            items = sorted(items, key=lambda i: (i.get(field) is None, i.get(field)), reverse=(direction.upper() == "DESC"))

    top_match = re.search(r"SELECT\s+TOP\s+(@\w+|\d+)", query, re.IGNORECASE)
    if top_match:
        raw = top_match.group(1)
        n = _param_value(raw, parameters) if raw.startswith("@") else int(raw)
        items = items[:int(n)]

    offset_limit_match = re.search(r"OFFSET\s+(\d+)\s+LIMIT\s+(\d+)", query, re.IGNORECASE)
    if offset_limit_match:
        offset, limit = int(offset_limit_match.group(1)), int(offset_limit_match.group(2))
        items = items[offset: offset + limit]

    return items
