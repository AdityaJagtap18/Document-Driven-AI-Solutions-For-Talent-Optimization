"""Structured observability for LLM calls: per-call token usage, latency,
cost, and success/failure. First piece of FDE Stage 31 (Observability) and
Stage 32 (Token Economics / AI FinOps) — before this, nothing in the repo
tracked tokens, cost, or latency anywhere; log.txt is 20,000+ lines of
freeform text with no structured metrics in it at all.

Always writes a local JSON-lines log (logs/llm_observability.jsonl) —
this works with zero configuration and is what actually runs today. Also
emits to Azure Application Insights if APPLICATIONINSIGHTS_CONNECTION_STRING
is configured (via secrets_loader, so Key Vault or .env both work) — failure
to reach Application Insights is caught and logged, never raised, so a
misconfigured/unprovisioned Application Insights resource never breaks a
pipeline run.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from secrets_loader import get_secret

LOG_PATH = Path(__file__).resolve().parent / "logs" / "llm_observability.jsonl"

_appinsights_state = None  # None = not yet attempted, True/False = attempted


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
        _appinsights_state = True
    except Exception as e:
        print(f"[WARN] Application Insights configuration failed, logging locally only: {e}")
        _appinsights_state = False
    return _appinsights_state


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
) -> dict:
    """Always writes one JSON line to logs/llm_observability.jsonl, and also
    emits to Application Insights (as a structured log record) if
    configured. Returns the record dict (useful for tests / callers that
    want to inspect what was recorded)."""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "label": label,
        "model": model,
        "latency_ms": round(latency_ms, 1) if latency_ms is not None else None,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cost_usd": cost_usd,
        "success": success,
        "error": error,
        "retries": retries,
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

    return record
