"""Local-disk persistence for the Azure report/snapshot containers.

Why this exists: observability_snapshots.py, azure_readiness.py and the
Azure Service Hub were all built around a "fetch real data only on an
explicit Refresh, persist it, then always serve the persisted copy"
architecture (see observability_snapshots.py's module docstring) -- but
the persistence target was Azure Cosmos DB (client.py's get_container()).
That means the one piece of this app meant to work WITHOUT re-querying
Azure on every page load still depended on Azure (Cosmos) being
configured and reachable just to show its own last-collected snapshot.

This module is a drop-in container implementation with the same minimal
surface repositories.py already calls (upsert_item/read_item/query_items/
delete_item) but backed by a plain JSON file on local disk, under
data/local_reports/<container>.json at the repo root. client.get_container()
routes a small allow-list of container names here instead of Cosmos --
see LOCAL_JSON_CONTAINERS below. Everything else (candidates, users, JDs,
...) is untouched and still goes to Cosmos exactly as before.

Only the handful of Cosmos SQL query shapes repositories.py actually
issues against these containers are supported (see _run_query) -- this is
not a general Cosmos emulator, just enough of one for these 5 containers'
real, fixed set of queries.
"""

import json
import re
import sys
import uuid
from pathlib import Path
from threading import Lock

_REPO_ROOT = Path(__file__).resolve().parent.parent
_STORE_DIR = _REPO_ROOT / "data" / "local_reports"

# Container names this module is allowed to serve from local disk instead
# of Cosmos -- deliberately a narrow, explicit list (the Azure report/
# snapshot surface only), not "every container falls back to local disk".
LOCAL_JSON_CONTAINERS = {
    "observability_snapshots",
    "observability_refresh_results",
    "integration_instances",
    "integration_health_checks",
    "integration_config_history",
}

_locks = {}


def _lock_for(name: str) -> Lock:
    return _locks.setdefault(name, Lock())


def _not_found_error_class():
    """repositories.py's existing callers (e.g. get_integration_instance())
    catch azure.cosmos.exceptions.CosmosResourceNotFoundError specifically --
    importing just that exception class is a static, no-network import (the
    azure-cosmos package is already a dependency for the Cosmos-backed
    containers), so read_item() below can raise the real class and every
    existing except-clause keeps working unmodified, with no live Cosmos
    connection ever made for these local-disk containers."""
    from azure.cosmos.exceptions import CosmosResourceNotFoundError
    return CosmosResourceNotFoundError


class LocalJSONContainer:
    """Mimics the tiny slice of the azure-cosmos ContainerProxy interface
    this repo's repositories.py actually calls, backed by one JSON file
    per container on local disk."""

    def __init__(self, name: str):
        self.name = name
        self._path = _STORE_DIR / f"{name}.json"

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


# ---------------------------------------------------------------------------
# A tiny, fixed-shape Cosmos-SQL reader -- only supports the exact clauses
# repositories.py's real queries against these 5 containers use: an
# optional "WHERE c.<field> = @param", an optional "TOP @param" or "TOP n",
# a required "ORDER BY c.<field> ASC|DESC", and an optional trailing
# "OFFSET n LIMIT m". Projection lists (SELECT c.a, c.b, ... instead of
# SELECT *) are intentionally ignored -- the full document is returned
# either way, which every caller in this repo already tolerates (they only
# ever read named fields off the result, never assert on their total set).
# ---------------------------------------------------------------------------
def _param_value(name: str, parameters: list):
    for p in parameters:
        if p.get("name") == name:
            return p.get("value")
    raise KeyError(f"Query references undeclared parameter '{name}'.")


def _run_query(items: list, query: str, parameters: list) -> list:
    where_match = re.search(r"WHERE\s+c\.(\w+)\s*=\s*(@\w+)", query, re.IGNORECASE)
    if where_match:
        field, param_name = where_match.group(1), where_match.group(2)
        value = _param_value(param_name, parameters)
        items = [i for i in items if i.get(field) == value]

    order_match = re.search(r"ORDER BY\s+c\.(\w+)\s+(ASC|DESC)", query, re.IGNORECASE)
    if order_match:
        field, direction = order_match.group(1), order_match.group(2).upper()
        items = sorted(items, key=lambda i: (i.get(field) is None, i.get(field)), reverse=(direction == "DESC"))

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
