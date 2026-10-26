"""F-02 (Tokenomics calculator and usage analytics) -- minimal slice.

Scope note (read before extending this file): the full F-02 feature
prompt describes a comprehensive multi-provider/multi-service usage
analytics platform (per-user tracking, exportable reports, cost-saving
recommendations, streaming/cache-hit reconciliation across every AI
surface). The real cost-tracking PRIMITIVE already existed before this
slice (observability.record_llm_call() + model_pricing.py, feeding the
existing AI Observability Center's all-time, unfiltered summary -- see
azure-functions/api_blueprint.py's _llm_observability_summary()). This
module is deliberately NOT a rewrite of that. It adds exactly the two
real things the roadmap identified as missing: (1) a real, PERIOD-scoped
usage aggregation (the existing summary is all-time only -- this adds
"this calendar month," with unknown/missing-cost records tracked
explicitly, per the feature prompt's own "track unknown/missing usage
explicitly" requirement), and (2) a real, versioned, admin-configurable
budget/threshold check against that period usage -- the one concrete gap
the roadmap called out ("Primitive already real... this is mostly a
frontend/budget-alert layer").

Everything else in the full prompt (per-user tracking, cost-saving
recommendations, multi-provider reconciliation beyond what already
exists, exportable historical reports) is explicitly deferred, not
silently dropped -- see docs/fde/IMPLEMENTATION_STATUS.md.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_REPO_ROOT))

DEFAULT_CONFIG = {"monthly_budget_usd": None, "warning_threshold_pct": 80}


def validate_config(config: dict) -> list:
    """Returns a list of human-readable validation errors -- empty list
    means valid. monthly_budget_usd may be None (budget disabled, the
    documented default) or a positive number; warning_threshold_pct must
    be an integer 1-100."""
    errors = []
    budget = config.get("monthly_budget_usd")
    if budget is not None and (not isinstance(budget, (int, float)) or budget <= 0):
        errors.append(f"monthly_budget_usd must be null (disabled) or a positive number, got {budget!r}")
    threshold = config.get("warning_threshold_pct")
    if not isinstance(threshold, int) or not (1 <= threshold <= 100):
        errors.append(f"warning_threshold_pct must be an integer 1-100, got {threshold!r}")
    return errors


def _is_current_utc_month(timestamp_str: str, now: datetime) -> bool:
    try:
        ts = datetime.fromisoformat(timestamp_str)
    except (TypeError, ValueError):
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.year == now.year and ts.month == now.month


def compute_monthly_usage(records: list, now: datetime = None) -> dict:
    """Real, period-scoped aggregation over the real llm_observability.
    jsonl records -- filtered to the current UTC calendar month, unlike
    the existing all-time _llm_observability_summary(). Tracks records
    with missing/unknown cost EXPLICITLY (a separate count, never folded
    silently into total_cost_usd as if it were 0 -- a $0 figure and an
    "unknown, N records" figure mean very different things to someone
    reading a budget status).

    cache_hit_call_count (F-18): calls served from llm_cache.py's local
    response cache -- the real model was never invoked for these, so
    their cost_usd/tokens are genuinely 0, already reflected correctly
    in total_cost_usd/total_tokens. Counted separately so a budget
    status can show "N calls avoided this month" as real evidence of
    the caching feature paying for itself, not mixed in unlabeled."""
    now = now or datetime.now(timezone.utc)
    total_cost = 0.0
    total_tokens = 0
    call_count = 0
    unknown_cost_count = 0
    cache_hit_count = 0
    by_label = {}

    for record in records:
        ts = record.get("timestamp")
        if not _is_current_utc_month(ts, now):
            continue
        call_count += 1
        total_tokens += record.get("total_tokens") or 0
        cost = record.get("cost_usd")
        label = record.get("label", "unknown")
        label_stats = by_label.setdefault(label, {"calls": 0, "tokens": 0, "cost_usd": 0.0, "unknown_cost_calls": 0, "cache_hit_calls": 0})
        label_stats["calls"] += 1
        label_stats["tokens"] += record.get("total_tokens") or 0
        if record.get("cache_hit"):
            cache_hit_count += 1
            label_stats["cache_hit_calls"] += 1
        if cost is None:
            unknown_cost_count += 1
            label_stats["unknown_cost_calls"] += 1
        else:
            total_cost += cost
            label_stats["cost_usd"] += cost

    return {
        "period": now.strftime("%Y-%m"),
        "call_count": call_count,
        "total_tokens": total_tokens,
        "total_cost_usd": round(total_cost, 6),
        "unknown_cost_call_count": unknown_cost_count,
        "cache_hit_call_count": cache_hit_count,
        "by_label": by_label,
    }


def check_budget_status(usage: dict, config: dict) -> dict:
    """Compares real period usage against the real active config.
    Returns status "not_configured" (no budget set -- the honest default,
    never a fabricated "ok"), "ok", "warning" (>= warning_threshold_pct),
    or "exceeded" (>= 100%). Never divides by zero or fabricates a
    percentage when no budget is configured."""
    budget = config.get("monthly_budget_usd") if config else None
    if budget is None:
        return {
            "configured": False, "status": "not_configured",
            "budget_usd": None, "spent_usd": usage["total_cost_usd"], "percent_used": None,
            "unknown_cost_call_count": usage["unknown_cost_call_count"],
        }

    spent = usage["total_cost_usd"]
    percent_used = round((spent / budget) * 100, 2)
    threshold = config.get("warning_threshold_pct", DEFAULT_CONFIG["warning_threshold_pct"])
    if percent_used >= 100:
        status = "exceeded"
    elif percent_used >= threshold:
        status = "warning"
    else:
        status = "ok"

    return {
        "configured": True, "status": status,
        "budget_usd": budget, "spent_usd": spent, "percent_used": percent_used,
        "unknown_cost_call_count": usage["unknown_cost_call_count"],
    }
