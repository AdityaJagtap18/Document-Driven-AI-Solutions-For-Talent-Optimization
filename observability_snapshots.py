"""Unified Observability: snapshot collection + persistence.

Core principle (per the Unified Full-Stack Observability spec): fetch real
telemetry only when a refresh is explicitly triggered, persist the result,
then serve the persisted snapshot for every normal page load -- never
re-query Azure (or any other real source) just because someone opened the
dashboard. A failed source during a refresh never erases that source's
last-known-good data from the snapshot; it's recorded as a failure and the
snapshot keeps whatever the previous successful collection had.

This module does not invent any new telemetry collection -- every source
function here is a thin wrapper around an already-real, already-verified
data source built earlier (azure_monitor_query.py, azure_readiness.py,
observability.py's local JSONL summary, the resume-processing/comms-
delivery reports). What's new here is ONLY the persist-and-serve-from-
snapshot architecture, not the underlying telemetry.

Explicitly NOT implemented (stated here, not silently skipped): host/
container CPU-memory-disk metrics (this runs as a local `func start`
process, not a deployed/containerized service -- there is no real metrics
source for this), frontend real-user-monitoring (no frontend error/
performance instrumentation exists in this repo), synthetic/external
uptime probing (would require a new, separately-hosted prober --
out of scope), and distributed OpenTelemetry trace propagation beyond
this repo's own existing span model (observability.py's trace_id/span_id
pairs are not W3C traceparent-propagated across a service mesh, since
there is no service mesh here)."""

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "context-lake"))

import repositories  # noqa: E402

_SOURCE_TIMEOUT_SECONDS = 25


def _now():
    return datetime.now(timezone.utc).isoformat()


def _collect_one(source_id: str, fetch_fn):
    """Runs one source's real collection, never letting an exception
    escape -- a broken source becomes a recorded FAILED result, not a
    crashed refresh. Real wall-clock duration recorded for real (useful
    for spotting a source that's gone slow, not just failed outright)."""
    import time

    started = time.monotonic()
    try:
        data = fetch_fn()
        status = "not_configured" if isinstance(data, dict) and data.get("_not_configured") else "success"
        return {
            "source_id": source_id, "status": status, "data": data,
            "error": None, "duration_ms": round((time.monotonic() - started) * 1000, 1),
        }
    except Exception as e:
        return {
            "source_id": source_id, "status": "failed", "data": None,
            "error": str(e)[:500], "duration_ms": round((time.monotonic() - started) * 1000, 1),
        }


def _azure_sources(hours: float):
    import azure_monitor_query as amq

    def _mark(fn):
        result = fn()
        if not amq.is_configured():
            result["_not_configured"] = True
        return result

    return {
        "azure_telemetry_health": lambda: _mark(amq.get_telemetry_health),
        "azure_request_volume": lambda: _mark(lambda: amq.get_request_volume(hours=hours)),
        "azure_latency": lambda: _mark(lambda: amq.get_latency_percentiles(hours=hours)),
        "azure_ai_usage": lambda: _mark(lambda: amq.get_ai_usage(hours=hours)),
        "azure_recent_errors": lambda: _mark(lambda: amq.get_recent_errors(hours=hours)),
    }


def _service_health_source():
    import az_cli
    import azure_integrations
    import azure_readiness

    az_status = az_cli.get_account_status()
    instances = repositories.list_integration_instances()
    services = []
    for i in instances:
        latest = None
        checks = repositories.list_integration_health_checks(i["id"], limit=1)
        if checks:
            latest = checks[0]
        services.append(azure_readiness.readiness_for_instance(i, latest_check=latest, az_account_status=az_status))
    registered = {s["adapter_type"] for s in services}
    for adapter_type, adapter in azure_integrations.ADAPTER_TYPES.items():
        if adapter_type in registered:
            continue
        verdict = azure_readiness.compute_readiness(adapter_type, check_result=None, az_authenticated=az_status.get("authenticated"))
        services.append({"adapter_type": adapter_type, "service_name": adapter["name"], "status": verdict["status"], "reason": verdict["reason"]})
    return {"services": services, "az_cli_authenticated": az_status.get("authenticated")}


def _local_observability_source():
    import observability
    import json as _json

    records = []
    if observability.TRACE_LOG_PATH.exists():
        with open(observability.TRACE_LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(_json.loads(line))
                except _json.JSONDecodeError:
                    continue
    by_workflow = {}
    for r in records:
        wf = r.get("workflow") or "unknown"
        stats = by_workflow.setdefault(wf, {"total": 0, "succeeded": 0, "failed": 0, "durations": []})
        stats["total"] += 1
        if r.get("status") == "succeeded":
            stats["succeeded"] += 1
        elif r.get("status") == "failed":
            stats["failed"] += 1
        if r.get("duration_ms") is not None:
            stats["durations"].append(r["duration_ms"])
    for wf, stats in by_workflow.items():
        stats["avg_duration_ms"] = round(sum(stats["durations"]) / len(stats["durations"]), 1) if stats["durations"] else None
        del stats["durations"]
    return {"total_spans": len(records), "by_workflow": by_workflow}


def _local_llm_calls_source():
    import json as _json
    from pathlib import Path as _Path

    log_path = _REPO_ROOT / "logs" / "llm_observability.jsonl"
    records = []
    if log_path.exists():
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(_json.loads(line))
                    except _json.JSONDecodeError:
                        pass
    by_label = {}
    total_tokens = total_cost = 0
    for r in records:
        label = r.get("label") or "unlabeled"
        stats = by_label.setdefault(label, {"calls": 0, "tokens": 0, "cost_usd": 0})
        stats["calls"] += 1
        stats["tokens"] += r.get("total_tokens") or 0
        stats["cost_usd"] += r.get("cost_usd") or 0
        total_tokens += r.get("total_tokens") or 0
        total_cost += r.get("cost_usd") or 0
    return {"total_calls": len(records), "total_tokens": total_tokens, "total_cost_usd": round(total_cost, 6), "by_label": by_label}


def _document_processing_source():
    import observability
    import json as _json

    records = []
    if observability.TRACE_LOG_PATH.exists():
        with open(observability.TRACE_LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(_json.loads(line))
                    except _json.JSONDecodeError:
                        pass
    extraction_spans = [r for r in records if r.get("stage") == "document_extraction"]
    failed = [r for r in extraction_spans if r.get("status") == "failed"]
    return {
        "total_processing_attempts": len(extraction_spans),
        "succeeded": sum(1 for r in extraction_spans if r.get("status") == "succeeded"),
        "failed_count": len(failed),
    }


def _comms_delivery_source():
    deliveries = repositories.list_all_comms_deliveries(limit=500)
    by_channel = {}
    for d in deliveries:
        channel = d.get("channel", "unknown")
        stats = by_channel.setdefault(channel, {"submitted": 0, "provider_accepted": 0, "failed": 0})
        stats["submitted"] += 1
        if d.get("outcome") == "provider_accepted":
            stats["provider_accepted"] += 1
        elif d.get("outcome") == "failed":
            stats["failed"] += 1
    return {"count": len(deliveries), "by_channel": by_channel}


def all_sources(hours: float = 24.0) -> dict:
    """The full real source registry this refresh collects from. Each
    value is a zero-arg callable; errors inside any one of them are
    caught by _collect_one(), never here."""
    sources = {}
    sources.update(_azure_sources(hours))
    sources["service_health"] = _service_health_source
    sources["local_ai_traces"] = _local_observability_source
    sources["local_llm_calls"] = _local_llm_calls_source
    sources["document_processing"] = _document_processing_source
    sources["comms_delivery"] = _comms_delivery_source
    return sources


def _is_soft_unavailable(result: dict) -> bool:
    """True when a source's fetch_fn returned NORMALLY (no exception, so
    _collect_one marked it "success") but its own real payload reports
    `"available": False` -- the shape azure_monitor_query.py's functions
    use to report a real underlying failure (e.g. an expired Azure CLI
    Temporary Access Pass breaking the Log Analytics query) WITHOUT
    raising, per that module's own "never raise out to a caller" design.

    REAL BUG this fixes: before this check existed, a soft-unavailable
    result was merged as a genuine "success" -- so once Log Analytics
    started failing, every subsequent refresh would overwrite any
    earlier REAL successful data with an empty "unavailable" payload,
    and the last_known_good fallback (below) never engaged, because
    that only triggered on an actual exception. The dashboard would
    then show "Unavailable" forever, even after a prior refresh had
    real data, with no way to recover it short of Log Analytics working
    again on the exact refresh that happens to run. Treating this
    exactly like a failure for MERGING purposes (while still recording
    the real "success" collection outcome in the per-source refresh-
    result log, since the round trip itself did succeed) fixes that:
    real data, once obtained, is preserved and shown until a future
    refresh actually succeeds with fresh data -- not silently erased by
    an intervening soft failure."""
    return (
        result["status"] == "success"
        and isinstance(result["data"], dict)
        and result["data"].get("available") is False
    )


def run_refresh(triggered_by: str, hours: float = 24.0, source_ids: list = None) -> dict:
    """Collects from every real source (or only `source_ids` if given --
    single-source refresh), persists one result row per source
    (observability_refresh_results) and one rolled-up snapshot document
    (observability_snapshots). A source that fails (or soft-fails -- see
    _is_soft_unavailable()) is recorded as failed with its error -- the
    snapshot keeps the PREVIOUS successful result for that source rather
    than overwriting it with nothing, so one bad source never erases
    good data for the rest."""
    refresh_run_id = str(uuid.uuid4())
    started_at = _now()

    previous = repositories.get_latest_observability_snapshot() or {"sources": {}}
    sources_registry = all_sources(hours)
    if source_ids:
        sources_registry = {k: v for k, v in sources_registry.items() if k in source_ids}

    merged_sources = dict(previous.get("sources", {}))
    succeeded = failed = skipped = 0

    for source_id, fetch_fn in sources_registry.items():
        result = _collect_one(source_id, fetch_fn)
        soft_unavailable = _is_soft_unavailable(result)
        repositories.append_observability_refresh_result({
            "refresh_run_id": refresh_run_id, "source_id": source_id,
            "status": "success" if result["status"] == "success" else result["status"],
            "error": (result["data"].get("detail") if soft_unavailable else result["error"]),
            "duration_ms": result["duration_ms"], "collected_at": _now(),
        })
        if result["status"] == "success" and not soft_unavailable:
            succeeded += 1
            merged_sources[source_id] = {
                "status": "success", "data": result["data"], "collected_at": _now(),
                "collected_by_refresh_run_id": refresh_run_id,
            }
        elif result["status"] == "not_configured":
            skipped += 1
            merged_sources[source_id] = {"status": "not_configured", "data": result["data"], "collected_at": _now()}
        else:
            failed += 1
            stale = merged_sources.get(source_id)
            error_text = result["data"].get("detail") if soft_unavailable else result["error"]
            merged_sources[source_id] = {
                "status": "failed", "error": error_text,
                "last_known_good": stale if stale and stale.get("status") == "success" else None,
                "attempted_at": _now(),
            }

    completed_at = _now()
    snapshot = {
        "id": refresh_run_id, "refresh_run_id": refresh_run_id,
        "triggered_by": triggered_by, "started_at": started_at, "completed_at": completed_at,
        "collected_at": completed_at, "hours_window": hours,
        "succeeded_count": succeeded, "failed_count": failed, "skipped_count": skipped,
        "sources": merged_sources,
    }
    repositories.save_observability_snapshot(snapshot)
    return snapshot


def get_snapshot() -> dict:
    """Pure persisted-data read -- NEVER calls a real source. This is
    what every normal dashboard page load should call; run_refresh() is
    only for an explicit user-triggered Refresh action."""
    snapshot = repositories.get_latest_observability_snapshot()
    if snapshot is None:
        return {
            "id": None, "collected_at": None, "sources": {},
            "detail": "No refresh has ever been run. Click Refresh to collect real data for the first time.",
        }
    return snapshot
