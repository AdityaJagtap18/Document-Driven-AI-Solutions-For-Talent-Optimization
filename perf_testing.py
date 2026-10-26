"""F-11 (comprehensive application performance and load testing) --
minimal slice.

Scope note (read before extending this file): the full F-11 feature
prompt describes a comprehensive performance program across every
subsystem (frontend, backend, database, AI/LLM, resume parsing,
matching, tokenomics, chatbots, observability, reporting, uploads) with
load/stress/spike/endurance/scalability test types and configurable
acceptance thresholds. This repository runs as a SINGLE local `func
start` process on a developer machine (confirmed by inspecting
observability_snapshots.py's own documented scope -- "this runs as a
local func start process, not a deployed/containerized service"), not a
scaled cloud deployment, so "stress/spike/scalability at production
concurrency" is not a real, honest claim this environment can make
evidence for.

This slice implements exactly the real, safe, defensible core: a small,
hard-capped load-test harness targeting an EXPLICIT ALLOWLIST of safe
endpoints only (SAFE_SCENARIOS) -- real HTTP calls against the real
local Functions host, measuring real p50/p95/p99 latency, throughput,
and failure rate. The allowlist deliberately excludes every real AI/LLM-
backed endpoint (chat, interview-question generation, resume parsing,
RAG preview, evaluation judges) -- per this feature's own explicit "do
not incur unexpected cloud or API costs" safety requirement, confirmed
by cross-referencing every allowlisted path against
observability.record_llm_call() call sites in azure-functions/
api_blueprint.py to verify none of them can trigger a real, billable
model call. Concurrency and total-request-count are both hard-capped
(DEFAULT_MAX_CONCURRENCY/DEFAULT_MAX_REQUESTS) so even a misconfigured
caller cannot accidentally hammer the real host or real Cosmos DB.

Deliberately deferred (see docs/fde/IMPLEMENTATION_STATUS.md): stress/
spike/endurance test TYPES (this slice is load-testing only), frontend
responsiveness testing, database-query-level profiling, AI-specific
token/latency load testing (would incur real API cost), a scheduled/
recurring run mechanism, and a frontend UI (this is operational tooling
for engineering, not a product capability end users configure -- same
precedent as F-12's Selenium E2E smoke test, which also has no API
endpoint, permission, or UI surface).
"""

import concurrent.futures
import statistics
import time
import urllib.error
import urllib.request

# The real, explicitly safe (read-only, non-destructive, never AI/LLM-
# backed) endpoints this harness is allowed to load-test. Adding an
# endpoint here is a deliberate safety decision, not just a convenience
# -- see the module docstring for why AI-backed endpoints are excluded.
SAFE_SCENARIOS = {
    "health": {"method": "GET", "path": "/api/health", "auth_required": False},
    "candidates_list": {"method": "GET", "path": "/api/candidates", "auth_required": True},
    "pipeline_config": {"method": "GET", "path": "/api/pipeline-config", "auth_required": True},
    "observability_summary": {"method": "GET", "path": "/api/observability/summary", "auth_required": True},
}

DEFAULT_MAX_CONCURRENCY = 20
DEFAULT_MAX_REQUESTS = 200
DEFAULT_TIMEOUT_SECONDS = 10


def compute_percentiles(latencies_ms: list) -> dict:
    """Real nearest-rank percentiles over real recorded latencies --
    never fabricates a value for an empty sample (reports None, with
    sample_size: 0, instead of a misleading 0ms)."""
    if not latencies_ms:
        return {"p50": None, "p95": None, "p99": None, "avg": None, "min": None, "max": None, "sample_size": 0}
    sorted_vals = sorted(latencies_ms)

    def _pct(p):
        idx = min(len(sorted_vals) - 1, int(round(p * (len(sorted_vals) - 1))))
        return round(sorted_vals[idx], 1)

    return {
        "p50": _pct(0.50), "p95": _pct(0.95), "p99": _pct(0.99),
        "avg": round(statistics.mean(sorted_vals), 1),
        "min": round(min(sorted_vals), 1), "max": round(max(sorted_vals), 1),
        "sample_size": len(sorted_vals),
    }


def summarize_run(results: list) -> dict:
    """Aggregates a real list of per-request results (see
    run_load_scenario()'s return shape) into the real report figures:
    total/succeeded/failed counts, failure rate, real throughput
    (requests actually completed / real wall-clock span), and latency
    percentiles over SUCCEEDED requests only (a failed request's
    latency-to-failure is a different signal, not conflated here)."""
    total = len(results)
    succeeded = [r for r in results if r["success"]]
    failed = [r for r in results if not r["success"]]
    latencies = [r["latency_ms"] for r in succeeded]

    if results:
        span_s = max(r["completed_at"] for r in results) - min(r["started_at"] for r in results)
    else:
        span_s = 0

    return {
        "total_requests": total, "succeeded": len(succeeded), "failed": len(failed),
        "failure_rate": round(len(failed) / total, 4) if total else None,
        "throughput_rps": round(total / span_s, 2) if span_s > 0 else None,
        "latency_ms": compute_percentiles(latencies),
        "errors": sorted({r["error"] for r in failed if r["error"]}),
    }


def _make_request(base_url: str, scenario: dict, headers: dict, epoch: float) -> dict:
    url = base_url.rstrip("/") + scenario["path"]
    req = urllib.request.Request(url, method=scenario["method"], headers=headers or {})
    started_at = time.monotonic() - epoch
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT_SECONDS) as resp:
            resp.read()
            status = resp.status
        latency_ms = (time.monotonic() - start) * 1000
        return {
            "success": 200 <= status < 300, "status_code": status, "latency_ms": latency_ms,
            "started_at": started_at, "completed_at": time.monotonic() - epoch, "error": None,
        }
    except urllib.error.HTTPError as e:
        latency_ms = (time.monotonic() - start) * 1000
        return {
            "success": False, "status_code": e.code, "latency_ms": latency_ms,
            "started_at": started_at, "completed_at": time.monotonic() - epoch, "error": f"HTTP {e.code}",
        }
    except Exception as e:
        latency_ms = (time.monotonic() - start) * 1000
        return {
            "success": False, "status_code": None, "latency_ms": latency_ms,
            "started_at": started_at, "completed_at": time.monotonic() - epoch, "error": str(e)[:200],
        }


def run_load_scenario(base_url: str, scenario_key: str, num_requests: int, concurrency: int, auth_token: str = None) -> list:
    """Runs `num_requests` real HTTP calls against the real `base_url`
    for the allowlisted `scenario_key`, with up to `concurrency` in
    flight at once (both hard-capped below their DEFAULT_MAX_* values,
    regardless of what the caller asks for). Raises ValueError for a
    non-allowlisted scenario or a missing auth_token where one is
    required -- never silently skips the safety check."""
    if scenario_key not in SAFE_SCENARIOS:
        raise ValueError(f"'{scenario_key}' is not a safe, allowlisted load-test scenario. Allowed: {sorted(SAFE_SCENARIOS)}.")
    scenario = SAFE_SCENARIOS[scenario_key]
    if scenario["auth_required"] and not auth_token:
        raise ValueError(f"Scenario '{scenario_key}' requires a real auth_token.")

    num_requests = max(1, min(num_requests, DEFAULT_MAX_REQUESTS))
    concurrency = max(1, min(concurrency, DEFAULT_MAX_CONCURRENCY))
    headers = {"Authorization": f"Bearer {auth_token}"} if scenario["auth_required"] else {}

    epoch = time.monotonic()
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(_make_request, base_url, scenario, headers, epoch) for _ in range(num_requests)]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())
    return results
