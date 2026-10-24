"""Real Azure Monitor / Log Analytics QUERY-BACK for the Azure Observability
Report page. Everything in observability.py only ever WRITES telemetry
(local JSONL always, Cosmos best-effort, Application Insights push via
OpenTelemetry logging handler) -- nothing in this repo read it back before
this module. This is the read side: real KQL queries against the real Log
Analytics workspace behind this environment's Application Insights resource
(`appi-cdpi-audit1` / `law-cdpi-audit1`), via the `azure-monitor-query` SDK.

Every `record_ai_trace()`/`record_llm_call()` call in observability.py logs
through `logging.getLogger("cdpi.ai_trace"/"cdpi.llm_observability").info(
message, extra={"custom_dimensions": record})` -- the Azure Monitor
OpenTelemetry distro maps that `custom_dimensions` dict into the `traces`
table's `customDimensions` column in Log Analytics, and `message` is
literally "ai_trace" or "llm_call", which is how queries below tell the two
kinds of rows apart.

Fails closed, like az_cli.py and secrets_loader.get_keyvault_status(): no
`azure-monitor-query` package, no workspace configured, no credential, a
query timeout, or an authorization failure ALL come back as a clear
{"available": False, "reason": ...} shape -- never raise out to a caller,
and never substitute a fabricated/zero value for a real query failure. A
genuine zero (the query ran, no matching rows) is represented differently
from "the query could not run at all" everywhere in this module.
"""

import os
from datetime import datetime, timedelta, timezone

import secrets_loader

_QUERY_TIMEOUT_SECONDS = 20
_credential_cache = {}


def _az_cli_credential():
    """AzureCliCredential, reusing whatever `az login` session is already
    active -- same auth story as az_cli.py and the rest of this repo's
    Azure CLI-dependent features, so this doesn't need a second,
    separately-configured identity. Cached per-process (credential
    objects are cheap but re-creating them per call is pointless)."""
    if "cli" in _credential_cache:
        return _credential_cache["cli"]
    try:
        from azure.identity import AzureCliCredential
        cred = AzureCliCredential()
        _credential_cache["cli"] = cred
        return cred
    except ImportError:
        return None


def workspace_id() -> str:
    return (secrets_loader.get_secret("AZURE_LOG_ANALYTICS_WORKSPACE_ID") or "").strip()


def is_configured() -> bool:
    return bool(workspace_id())


def _logs_client():
    try:
        from azure.monitor.query import LogsQueryClient
    except ImportError:
        return None, "dependency_missing"
    credential = _az_cli_credential()
    if credential is None:
        return None, "dependency_missing"
    try:
        return LogsQueryClient(credential), None
    except Exception:
        return None, "client_init_failed"


def _categorize_query_error(exception) -> str:
    try:
        from azure.core.exceptions import ClientAuthenticationError, HttpResponseError, ServiceRequestError
    except ImportError:
        return "unknown_error"
    if isinstance(exception, ClientAuthenticationError):
        return "authentication_failed"
    if isinstance(exception, HttpResponseError):
        status = getattr(exception, "status_code", None)
        if status == 403:
            return "authorization_denied"
        if status == 404:
            return "workspace_not_found"
        return "query_failed"
    if isinstance(exception, ServiceRequestError):
        return "endpoint_unreachable"
    return "unknown_error"


def run_kql_query(kql: str, hours: float = 24.0) -> dict:
    """Real query execution against the real Log Analytics workspace.
    Returns {"available": bool, "columns": [...], "rows": [[...], ...],
    "reason": str|None, "detail": str}. `rows`/`columns` are only ever
    populated on a genuinely successful real query -- never on any error
    path, never partially fabricated."""
    if not is_configured():
        return {"available": False, "columns": [], "rows": [], "reason": "not_configured",
                "detail": "AZURE_LOG_ANALYTICS_WORKSPACE_ID is not set."}
    client, reason = _logs_client()
    if client is None:
        return {"available": False, "columns": [], "rows": [], "reason": reason or "client_unavailable",
                "detail": "Could not create a Log Analytics query client (missing azure-monitor-query/azure-identity, or no az CLI session)."}
    try:
        from azure.monitor.query import LogsQueryStatus
        response = client.query_workspace(
            workspace_id(), kql, timespan=timedelta(hours=hours),
            server_timeout=_QUERY_TIMEOUT_SECONDS,
        )
        if response.status != LogsQueryStatus.SUCCESS:
            partial_error = getattr(response, "partial_error", None)
            return {"available": False, "columns": [], "rows": [], "reason": "partial_failure",
                    "detail": f"Log Analytics query returned a partial/failed status: {partial_error}"}
        table = response.tables[0] if response.tables else None
        if table is None:
            return {"available": True, "columns": [], "rows": [], "reason": None,
                    "detail": "Query succeeded with no result table (no matching data)."}
        return {"available": True, "columns": list(table.columns), "rows": [list(r) for r in table.rows],
                "reason": None, "detail": f"Real Log Analytics query, {len(table.rows)} row(s)."}
    except Exception as e:
        category = _categorize_query_error(e)
        return {"available": False, "columns": [], "rows": [], "reason": category,
                "detail": f"Log Analytics query failed ({category}): {str(e)[:300]}"}


def _rows_as_dicts(result: dict) -> list:
    if not result["available"]:
        return []
    cols = result["columns"]
    return [dict(zip(cols, row)) for row in result["rows"]]


# This environment's Application Insights resource is WORKSPACE-BASED, so
# ingested log records land in `AppTraces` (not the classic `traces` table),
# with the record's timestamp in `TimeGenerated` and the structured message
# in `Message` -- confirmed directly against the real workspace while
# building this module (`az monitor log-analytics query`), not assumed from
# documentation.
#
# REAL, CONFIRMED LIMITATION (documented, not silently worked around):
# observability.py logs via `logger.info(message, extra={"custom_dimensions":
# record})`, where `record` is a nested dict (it has a `metadata` sub-dict).
# OpenTelemetry's logging-attribute model only supports flat, primitive
# attribute values -- it cannot represent a nested dict as multiple
# structured Log Analytics columns, so the Azure Monitor exporter falls back
# to Python's str()/repr() of the whole `record` dict as ONE string value
# under the `custom_dimensions` key inside the JSON `Properties` column.
# That string is Python-repr syntax (single-quoted, `None`/`True`/`False`),
# NOT valid JSON -- `parse_json()` cannot parse it directly. Every query
# below extracts individual fields from that string via KQL `extract()`
# regexes instead. This works for the fields this module needs, but is
# inherently fragile (adding a field with an apostrophe in its value, for
# example, could break extraction) -- the real, durable fix is changing
# observability.py to log flat, JSON-safe attributes instead of one nested
# dict; see docs/AZURE_OBSERVABILITY.md's "Known gaps" section.
_CD_EXPR = 'tostring(parse_json(Properties).custom_dimensions)'


def _extract(field: str, cd_var: str = "cd") -> str:
    """KQL snippet pulling one field's value out of the Python-repr
    custom_dimensions string, with surrounding quotes stripped (trim()
    is a no-op on a bare None/True/False/numeric token, so this is safe
    for every value type) -- callers cast the result with todouble() as
    needed, or compare it directly as a string ("succeeded", "True", ...)."""
    return f"trim(\"'\", extract(@\"'{field}': ('[^']*'|None|True|False|[0-9.eE+-]+)\", 1, {cd_var}))"


def get_telemetry_health() -> dict:
    """Real ingestion evidence: the timestamp of the most recent ai_trace
    OR llm_call row actually found in Log Analytics (AppTraces), queried
    over the last 24h. This is the thing that distinguishes "Azure Monitor
    is configured" from "Azure Monitor has actually ingested something
    real" -- a connection string alone never counts as READY here."""
    result = run_kql_query(
        'AppTraces | where Message in ("ai_trace", "llm_call") '
        '| summarize last_ingested = max(TimeGenerated)',
        hours=24,
    )
    if not result["available"]:
        return {"azure_configured": is_configured(), "ingestion_confirmed": False,
                "last_ingested_at": None, "reason": result["reason"], "detail": result["detail"]}
    rows = _rows_as_dicts(result)
    last_ingested = rows[0].get("last_ingested") if rows else None
    return {
        "azure_configured": True,
        "ingestion_confirmed": last_ingested is not None,
        "last_ingested_at": str(last_ingested) if last_ingested else None,
        "reason": None if last_ingested else "no_rows_in_window",
        "detail": "Real Log Analytics ingestion confirmed." if last_ingested
                   else "Workspace is configured and reachable, but no ai_trace/llm_call rows were found in the last 24h.",
    }


def get_request_volume(hours: float = 24.0, bucket_minutes: int = 60) -> dict:
    """Real request (trace) volume over time, succeeded vs. failed, bucketed."""
    kql = (
        'AppTraces | where Message == "ai_trace" '
        f'| extend cd = {_CD_EXPR} '
        f'| extend status = {_extract("status")} '
        f'| summarize total = count(), succeeded = countif(status == "succeeded"), '
        f'failed = countif(status == "failed") by timestamp = bin(TimeGenerated, {bucket_minutes}m) '
        '| order by timestamp asc'
    )
    result = run_kql_query(kql, hours=hours)
    if not result["available"]:
        return {"available": False, "reason": result["reason"], "detail": result["detail"], "buckets": []}
    buckets = [
        {"timestamp": str(r.get("timestamp")), "total": r.get("total", 0),
         "succeeded": r.get("succeeded", 0), "failed": r.get("failed", 0)}
        for r in _rows_as_dicts(result)
    ]
    return {"available": True, "detail": result["detail"], "buckets": buckets}


def get_latency_percentiles(hours: float = 24.0) -> dict:
    """Real p50/p95/p99 span duration from Azure-ingested ai_trace rows --
    distinct from the local-JSONL-sourced percentiles the existing AI
    Observability Center already shows (observability.TRACE_LOG_PATH),
    this is computed from what Azure Monitor actually ingested, which can
    legitimately differ (export lag, export failures)."""
    kql = (
        'AppTraces | where Message == "ai_trace" '
        f'| extend cd = {_CD_EXPR} '
        f'| extend duration_ms = todouble({_extract("duration_ms")}) '
        '| where isnotnull(duration_ms) '
        '| summarize count = count(), p50 = percentile(duration_ms, 50), '
        'p95 = percentile(duration_ms, 95), p99 = percentile(duration_ms, 99)'
    )
    result = run_kql_query(kql, hours=hours)
    if not result["available"]:
        return {"available": False, "reason": result["reason"], "detail": result["detail"]}
    rows = _rows_as_dicts(result)
    if not rows or not rows[0].get("count"):
        return {"available": True, "sample_size": 0, "p50": None, "p95": None, "p99": None,
                "detail": "Query succeeded; no spans with a duration were found in this window."}
    r = rows[0]
    sample_size = r.get("count", 0)
    if sample_size < 20:
        return {"available": True, "sample_size": sample_size, "p50": None, "p95": None, "p99": None,
                "detail": f"Only {sample_size} sample(s) in this window -- too few for a reliable percentile (minimum 20)."}
    return {"available": True, "sample_size": sample_size,
            "p50": round(r.get("p50"), 1), "p95": round(r.get("p95"), 1), "p99": round(r.get("p99"), 1),
            "detail": result["detail"]}


def get_ai_usage(hours: float = 24.0) -> dict:
    """Real per-model request count, token usage, and cost -- from
    Azure-ingested llm_call rows."""
    kql = (
        'AppTraces | where Message == "llm_call" '
        f'| extend cd = {_CD_EXPR} '
        f'| extend model = {_extract("model")}, provider = {_extract("provider")}, '
        f'tokens = todouble({_extract("total_tokens")}), cost = todouble({_extract("cost_usd")}), '
        f'succeeded = {_extract("success")} '
        '| summarize calls = count(), total_tokens = sum(tokens), total_cost_usd = sum(cost), '
        'failed = countif(succeeded == "False") by model, provider '
        '| order by calls desc'
    )
    result = run_kql_query(kql, hours=hours)
    if not result["available"]:
        return {"available": False, "reason": result["reason"], "detail": result["detail"], "by_model": []}
    by_model = [
        {"model": r.get("model"), "provider": r.get("provider"), "calls": r.get("calls", 0),
         "total_tokens": r.get("total_tokens"), "total_cost_usd": r.get("total_cost_usd"),
         "failed": r.get("failed", 0)}
        for r in _rows_as_dicts(result)
    ]
    return {"available": True, "detail": result["detail"], "by_model": by_model}


def get_recent_errors(hours: float = 24.0, limit: int = 20) -> dict:
    """Real recent failures (ai_trace status=failed, or llm_call
    success=False), most recent first, with correlation IDs for
    cross-referencing the existing Trace Explorer."""
    limit = min(max(int(limit), 1), 100)
    kql = (
        'AppTraces | where Message in ("ai_trace", "llm_call") '
        f'| extend cd = {_CD_EXPR} '
        f'| extend status = {_extract("status")}, succeeded = {_extract("success")}, '
        f'error = {_extract("error")}, workflow = {_extract("workflow")}, '
        f'stage = {_extract("stage")}, label = {_extract("label")}, '
        f'correlation_id = {_extract("correlation_id")} '
        '| where status == "failed" or succeeded == "False" '
        '| project timestamp = TimeGenerated, message = Message, workflow, stage, label, error, correlation_id '
        '| order by timestamp desc '
        f'| take {limit}'
    )
    def _none_as_none(v):
        return None if v in (None, "None") else v

    result = run_kql_query(kql, hours=hours)
    if not result["available"]:
        return {"available": False, "reason": result["reason"], "detail": result["detail"], "errors": []}
    errors = [
        {"timestamp": str(r.get("timestamp")), "source": r.get("message"), "workflow": _none_as_none(r.get("workflow")),
         "stage": _none_as_none(r.get("stage")), "label": _none_as_none(r.get("label")),
         "error": (_none_as_none(r.get("error")) or "")[:500],
         "correlation_id": _none_as_none(r.get("correlation_id"))}
        for r in _rows_as_dicts(result)
    ]
    return {"available": True, "detail": result["detail"], "errors": errors}
