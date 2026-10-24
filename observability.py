"""Structured observability for LLM calls (record_llm_call) and, as of the
AI Observability Center feature, for every SPAN (one pipeline stage) of an
AI workflow TRACE (record_ai_trace/trace_stage) -- per-call token usage,
latency, cost, success/failure, plus which pipeline stage ran (document
extraction, chunking, embedding generation, retrieval, LLM request,
structured-output validation, persistence, etc.), not just LLM token
counts. First piece of FDE Stage 31 (Observability) and Stage 32 (Token
Economics / AI FinOps) — before this, nothing in the repo tracked tokens,
cost, latency, or pipeline-stage outcomes anywhere; log.txt is 20,000+
lines of freeform text with no structured metrics in it at all.

TRACE/SPAN SCHEMA (OpenTelemetry-shaped, not a literal OTel SDK dependency
-- see this module's own "why not the real SDK" note below):
  - trace_id: groups every span belonging to ONE end-to-end workflow
    execution (e.g. one resume being parsed: extraction -> chunking ->
    embedding -> retrieval-ranking -> N summarization LLM calls -> merge
    LLM call -> output validation -> persistence are all ONE trace_id).
    A caller that wants several stages correlated generates one trace_id
    (new_trace_id(), or a meaningful existing id like a candidate_id) and
    passes it to every trace_stage()/record_ai_trace() call for that run.
    A caller that doesn't pass one gets a fresh trace_id per call --
    equivalent to "a trace with exactly one span" -- fully backward
    compatible with every call site that existed before this schema had
    a separate trace_id/span_id distinction.
  - span_id: unique per individual stage/call (never reused).
  - parent_span_id: optional, for sub-stage hierarchy within a trace.
  - correlation_id: an optional EXTERNAL business identifier (e.g. a
    candidate_id or resume filename) to search by -- distinct from
    trace_id, which is purely an internal grouping key with no meaning
    outside this telemetry system.

No separate "AITrace" summary document is written anywhere -- a trace is
COMPUTED by grouping its spans by trace_id at read time (see
azure-functions/api_blueprint.py's ai-observability endpoints). This
avoids a second write path (an AITrace doc that would need updating
every time a new span is added) that could drift from the span data
itself; the spans ARE the source of truth.

PERSISTENCE: always writes local JSON-lines logs (logs/llm_observability.jsonl,
logs/ai_traces.jsonl) -- this works with zero configuration and is what
actually runs today, and guarantees telemetry recording never breaks the
AI call it's observing even if every other sink is unavailable. ALSO
best-effort dual-writes each span to Cosmos DB's `ai_trace_spans`
container (partition /trace_id, so "every span of one trace" is a cheap
single-partition read) when Cosmos is configured -- same "fails soft,
CSV/JSONL write unaffected" pattern ResumeParser.py already uses for its
own Cosmos dual-write. Also emits to Azure Application Insights if
APPLICATIONINSIGHTS_CONNECTION_STRING is configured (via secrets_loader,
so Key Vault or .env both work) -- failure to reach either Cosmos or
Application Insights is caught and logged, never raised.

WHY NOT THE REAL OPENTELEMETRY SDK: this repo has no OTel SDK dependency
today (only azure-monitor-opentelemetry, commented out, dormant -- see
docs/AI_OBSERVABILITY_AZURE_HUB.md). Adopting the full `opentelemetry-sdk`
API (TracerProvider, SpanProcessor, exporters) for a Python script-based
pipeline with no running server process is a bigger dependency/ops change
than this pass takes on; this module's trace/span/parent_span_id/
correlation_id shape is deliberately OTel-COMPATIBLE (the same concepts,
the same field semantics) so that swapping in the real SDK later is a
backend-only change, not a reshuffle of every call site's trace_id/
span_id plumbing.

record_llm_call() ALSO writes an ai_traces.jsonl span (workflow="llm",
stage=label) itself, rather than every LLM call site needing to call both
record_llm_call() AND record_ai_trace() separately — one instrumentation
point, two views of the same event, per this feature's "avoid duplicate
implementations" requirement.
"""

import json
import logging
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from secrets_loader import get_secret

LOG_PATH = Path(__file__).resolve().parent / "logs" / "llm_observability.jsonl"
TRACE_LOG_PATH = Path(__file__).resolve().parent / "logs" / "ai_traces.jsonl"

_REPO_ROOT = Path(__file__).resolve().parent
_METADATA_VALUE_MAX_CHARS = 300  # bounded telemetry payload -- see _bound_metadata()

_appinsights_state = None  # None = not yet attempted, True/False = attempted


def new_trace_id() -> str:
    """Generates a fresh trace_id for a caller that wants to correlate
    several spans into one end-to-end workflow trace (e.g. one resume's
    full parse). Just a uuid4 -- exposed as its own function so call
    sites don't each need `import uuid`."""
    return str(uuid.uuid4())


_TELEMETRY_LOGGER_NAMES = ("cdpi.ai_trace", "cdpi.llm_observability")


def _appinsights_ready() -> bool:
    global _appinsights_state
    if _appinsights_state is not None:
        return _appinsights_state
    conn_str = get_secret("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not conn_str:
        _appinsights_state = False
        return False
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor

        configure_azure_monitor(connection_string=conn_str)
        # REAL BUG found and fixed in the Azure real-services audit pass:
        # configure_azure_monitor() attaches its OpenTelemetry
        # LoggingHandler to the ROOT logger but never raises any
        # logger's effective level. Python's root logger defaults to
        # WARNING, and this module's record_ai_trace()/record_llm_call()
        # both call logging.getLogger(name).info(...) -- every one of
        # those calls was being silently dropped by Python's own logging
        # module BEFORE the Azure Monitor handler ever saw it, for any
        # deployment that had Application Insights "configured" (SDK
        # initialized successfully) but still sent zero real telemetry.
        # Verified directly: logging.getLogger("cdpi.ai_trace").
        # isEnabledFor(logging.INFO) was False even after a successful
        # configure_azure_monitor() call, confirming "initializing the
        # SDK alone does not prove ingestion" -- this was the actual gap.
        for logger_name in _TELEMETRY_LOGGER_NAMES:
            logging.getLogger(logger_name).setLevel(logging.INFO)
        _appinsights_state = True
    except Exception as e:
        print(f"[WARN] Application Insights configuration failed, logging locally only: {e}")
        _appinsights_state = False
    return _appinsights_state


def _bound_metadata(metadata: dict) -> dict:
    """Caps every metadata value to _METADATA_VALUE_MAX_CHARS -- defense
    in depth against accidentally passing a raw prompt/resume/response
    string into telemetry (every real call site in this repo only passes
    small scalars -- model names, counts -- never raw text, but this
    bounds the payload regardless of what a future call site does)."""
    if not metadata:
        return {}
    bounded = {}
    for key, value in metadata.items():
        if isinstance(value, str) and len(value) > _METADATA_VALUE_MAX_CHARS:
            bounded[key] = value[:_METADATA_VALUE_MAX_CHARS] + "...[truncated]"
        else:
            bounded[key] = value
    return bounded


def _write_span_to_cosmos(record: dict) -> None:
    """Best-effort dual-write to Cosmos DB's ai_trace_spans container --
    same "fails soft, local write unaffected" pattern ResumeParser.py
    already uses for its own context-lake dual-write. Never raises."""
    try:
        sys.path.insert(0, str(_REPO_ROOT / "context-lake"))
        from client import get_container  # noqa: E402

        doc = dict(record)
        doc["id"] = record["span_id"]
        get_container("ai_trace_spans").upsert_item(doc)
    except Exception as e:
        print(f"[WARN] Cosmos DB write for AI trace span failed (local log entry was still written): {e}")


def record_llm_call(
    label: str,
    model: str = None,
    latency_ms: float = None,
    prompt_tokens: int = None,
    completion_tokens: int = None,
    total_tokens: int = None,
    cost_usd: float = None,
    success: bool = True,
    error: str = None,
    retries: int = 0,
    trace_id: str = None,
    parent_span_id: str = None,
    correlation_id: str = None,
    provider: str = "openai",
    prompt_version: str = None,
    cost_source: str = "langchain_openai_callback",
    workflow: str = "llm",
) -> dict:
    """Always writes one JSON line to logs/llm_observability.jsonl, and also
    emits to Application Insights (as a structured log record) if
    configured. Returns the record dict (useful for tests / callers that
    want to inspect what was recorded).

    cost_usd is computed by langchain_community.callbacks.get_openai_callback()
    upstream (see llm_utils.py) -- a real, provider-published-pricing-based
    calculation, not an invented number; cost_source documents exactly
    that, so a caller/UI never has to guess where a cost figure came
    from. trace_id/parent_span_id/correlation_id let this LLM call join a
    larger multi-stage trace (e.g. ResumeParser.py's per-resume run) --
    all optional and None by default, fully backward compatible with
    every call site that doesn't pass them."""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "label": label,
        "model": model,
        "provider": provider,
        "latency_ms": round(latency_ms, 1) if latency_ms is not None else None,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cost_usd": cost_usd,
        "cost_source": cost_source if cost_usd is not None else None,
        "success": success,
        "error": error,
        "retries": retries,
        "prompt_version": prompt_version,
    }

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

    if _appinsights_ready():
        try:
            logger = logging.getLogger("cdpi.llm_observability")
            logger.info("llm_call", extra={"custom_dimensions": record})
        except Exception as e:
            print(f"[WARN] Application Insights emit failed (local log entry was still written): {e}")

    record_ai_trace(
        workflow=workflow, stage=label, status="succeeded" if success else "failed",
        duration_ms=latency_ms,
        metadata={
            "model": model, "provider": provider, "total_tokens": total_tokens,
            "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
            "cost_usd": cost_usd, "cost_source": record["cost_source"],
            "retries": retries, "prompt_version": prompt_version,
        },
        error=error, trace_id=trace_id, parent_span_id=parent_span_id, correlation_id=correlation_id,
    )

    return record


# ---------------------------------------------------------------------------
# AI workflow trace spans -- one entry per pipeline STAGE (not just LLM
# calls): document extraction, chunking, embedding generation, retrieval/
# reranking, LLM calls (via record_llm_call above), structured-output
# validation, persistence, document verification, etc. See this module's
# top docstring for the full trace_id/span_id/parent_span_id/
# correlation_id schema.
# ---------------------------------------------------------------------------
def record_ai_trace(
    workflow: str,
    stage: str,
    status: str = "succeeded",
    duration_ms: float = None,
    metadata: dict = None,
    error: str = None,
    trace_id: str = None,
    span_id: str = None,
    parent_span_id: str = None,
    correlation_id: str = None,
) -> dict:
    """status is one of "started", "succeeded", "failed". Always writes one
    JSON line to logs/ai_traces.jsonl, best-effort dual-writes to Cosmos
    DB's ai_trace_spans container, and, if configured, emits to
    Application Insights. Returns the record dict. trace_id is generated
    (as a fresh, single-span trace) if not supplied; span_id is always
    generated fresh regardless -- a span's own identity is never reused
    even when it shares a trace_id with others. error text is NOT
    redacted here -- callers that might include secret-shaped text in an
    exception message should redact before calling (see
    azure_integrations.redact_secret() for that exact pattern)."""
    record = {
        "trace_id": trace_id or str(uuid.uuid4()),
        "span_id": span_id or str(uuid.uuid4()),
        "parent_span_id": parent_span_id,
        "correlation_id": correlation_id,
        "workflow": workflow,
        "stage": stage,
        "status": status,
        "duration_ms": round(duration_ms, 1) if duration_ms is not None else None,
        "metadata": _bound_metadata(metadata),
        "error": error,
        "logged_at": datetime.now(timezone.utc).isoformat(),
    }

    TRACE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(TRACE_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

    _write_span_to_cosmos(record)

    if _appinsights_ready():
        try:
            logger = logging.getLogger("cdpi.ai_trace")
            logger.info("ai_trace", extra={"custom_dimensions": record})
        except Exception as e:
            print(f"[WARN] Application Insights emit failed (local log entry was still written): {e}")

    return record


def run_connectivity_test() -> dict:
    """Explicit, admin-triggered test operation correlating a known
    correlation_id with the telemetry it produces -- "initializing the
    SDK alone does not prove ingestion," so this emits a REAL span
    (through the exact same record_ai_trace() path every production
    call uses, not a parallel test-only code path) and reports exactly
    what happened to it: whether it was written locally (always true)
    and whether it was additionally handed to the Application Insights
    exporter (_appinsights_ready()). This function cannot itself confirm
    the span was successfully INGESTED by Azure Monitor -- that requires
    querying Application Insights after its real (several-minute)
    ingestion latency, which needs a real provisioned resource this repo
    does not have in every environment. An admin who wants that last
    step of proof should search Application Insights' Logs/Transaction
    Search for the returned correlation_id a few minutes after calling
    this."""
    test_correlation_id = f"connectivity-test-{uuid.uuid4().hex[:12]}"
    appinsights_configured_before_call = _appinsights_ready()
    record = record_ai_trace(
        workflow="observability_self_test", stage="connectivity_test", status="succeeded",
        duration_ms=0.0, metadata={"purpose": "admin-triggered connectivity test, not real pipeline activity"},
        correlation_id=test_correlation_id,
    )
    return {
        "correlation_id": test_correlation_id,
        "trace_id": record["trace_id"],
        "span_id": record["span_id"],
        "written_to_local_log": True,
        "local_log_path": str(TRACE_LOG_PATH),
        "application_insights_configured": appinsights_configured_before_call,
        "application_insights_handed_off": appinsights_configured_before_call,
        "note": (
            "This confirms the span was handed to the Application Insights exporter; it does NOT confirm Azure "
            "actually ingested it (ingestion has its own latency and failure modes this process can't observe). "
            "Search Application Insights for this correlation_id after a few minutes to verify ingestion."
            if appinsights_configured_before_call else
            "Application Insights is not configured -- this span was written to the local log only, exactly like every real trace in this environment today."
        ),
    }


@contextmanager
def trace_stage(workflow: str, stage: str, metadata: dict = None, trace_id: str = None,
                 parent_span_id: str = None, correlation_id: str = None):
    """Context manager wrapping one pipeline stage/span: records a trace
    entry on exit, "succeeded" if the block completed without raising,
    "failed" (with the exception's message) if it raised -- the
    exception always still propagates, this only observes it. Usage:

        trace_id = observability.new_trace_id()
        with trace_stage("resume_parsing", "document_extraction", trace_id=trace_id):
            text = extract_text_from_pdf(path)
        with trace_stage("resume_parsing", "chunking_and_embedding", trace_id=trace_id):
            chunks = embed_and_index_resume_text(...)

    Both spans above share one trace_id, so the AI Observability Center's
    trace explorer shows them as one multi-stage trace for this resume,
    not two unrelated events. Pass trace_id=None (the default) for a
    standalone, single-span trace -- what every pre-existing call site
    that doesn't know about multi-stage traces still gets.

    Yields the span_id (a fresh uuid4) so the caller can pass it as a
    later span's parent_span_id if sub-stage hierarchy is useful."""
    span_id = str(uuid.uuid4())
    start = time.monotonic()
    try:
        yield span_id
    except Exception as e:
        record_ai_trace(
            workflow=workflow, stage=stage, status="failed",
            duration_ms=(time.monotonic() - start) * 1000, metadata=metadata,
            error=str(e), trace_id=trace_id, span_id=span_id, parent_span_id=parent_span_id,
            correlation_id=correlation_id,
        )
        raise
    else:
        record_ai_trace(
            workflow=workflow, stage=stage, status="succeeded",
            duration_ms=(time.monotonic() - start) * 1000, metadata=metadata,
            trace_id=trace_id, span_id=span_id, parent_span_id=parent_span_id,
            correlation_id=correlation_id,
        )
