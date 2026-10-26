"""F-11 (performance/load testing, minimal slice) -- real load-test
baseline runner.

Runs a small, hard-capped, SAFE (non-AI, non-destructive -- see
perf_testing.SAFE_SCENARIOS) load test against the real local Azure
Functions host, IF ONE IS ALREADY RUNNING on port 7071. This test does
NOT start the host itself (a `func start` process is slow and heavy to
spin up inside a routine test run -- unlike the frontend's lightweight
`python -m http.server`, used by tests/e2e/test_azure_readiness_smoke.py).

If nothing is listening on 7071, this reports BLOCKED (via pytest.skip
with a clear reason), not a false FAIL -- an environmental prerequisite
(the host isn't running right now) is not the same as the application
performing badly. When the host IS running, every assertion is a real
measurement of real HTTP round-trips against the real app -- never a
fabricated or assumed number.

Writes a real report to docs/fde/PERFORMANCE_BASELINE.md with the exact
figures measured, so the result is durable evidence, not just a pass/
fail in a CI log.

Run:
    pytest tests/perf/test_load_baseline.py -v -s
"""

import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT))

import perf_testing  # noqa: E402

_BASE_URL = "http://127.0.0.1:7071"
_REPORT_PATH = _REPO_ROOT / "docs" / "fde" / "PERFORMANCE_BASELINE.md"

# Real admin demo account -- see docs/fde/*.md for prior live-verification
# use of this same account throughout this engagement. Credential is
# never hardcoded/committed; set it in your shell before running this suite.
_ADMIN_USERNAME = os.environ.get("CDPI_ADMIN_USERNAME", "auditadmin")
_ADMIN_PASSWORD = os.environ.get("CDPI_ADMIN_PASSWORD")


def _port_is_open(port, host="127.0.0.1"):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def _get_real_admin_token():
    import json
    import urllib.request

    if not _ADMIN_PASSWORD:
        pytest.skip("BLOCKED: set CDPI_ADMIN_PASSWORD (and CDPI_ADMIN_USERNAME if not 'auditadmin') in your shell before running this suite.")
    req = urllib.request.Request(
        _BASE_URL + "/api/auth/login", method="POST",
        data=json.dumps({"username": _ADMIN_USERNAME, "password": _ADMIN_PASSWORD}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())["token"]


def _write_report(all_summaries: dict):
    lines = [
        "# Performance Baseline (F-11, minimal slice)",
        "",
        f"Generated {datetime.now(timezone.utc).isoformat()} by `tests/perf/test_load_baseline.py` against the real "
        "local Azure Functions host (`http://127.0.0.1:7071`). Every figure below is a real measured HTTP round-trip "
        "-- never fabricated or assumed. Scope: see `perf_testing.py`'s module docstring for exactly which endpoints "
        "are load-tested (an explicit, AI-call-free safety allowlist) and why.",
        "",
        "| Scenario | Requests | Concurrency | Succeeded | Failed | Failure Rate | p50 (ms) | p95 (ms) | p99 (ms) | Throughput (req/s) |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for scenario_key, run in all_summaries.items():
        s = run["summary"]
        lat = s["latency_ms"]
        lines.append(
            f"| {scenario_key} | {s['total_requests']} | {run['concurrency']} | {s['succeeded']} | {s['failed']} | "
            f"{s['failure_rate']} | {lat['p50']} | {lat['p95']} | {lat['p99']} | {s['throughput_rps']} |"
        )
    lines.append("")
    _REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


@pytest.fixture(scope="module")
def running_host():
    if not _port_is_open(7071):
        pytest.skip("BLOCKED: no Azure Functions host is listening on 127.0.0.1:7071 right now. Start it (`func start --port 7071` in azure-functions/) to run this real load-test baseline.")
    return _BASE_URL


class TestLoadBaseline:
    def test_golden_health_endpoint_under_light_concurrent_load(self, running_host):
        results = perf_testing.run_load_scenario(running_host, "health", num_requests=30, concurrency=10)
        summary = perf_testing.summarize_run(results)
        assert summary["total_requests"] == 30
        # A generous, non-production-grade threshold -- this is a single
        # local dev process, not a scaled deployment; the point is
        # catching a real regression (e.g. an accidental blocking call
        # added to the hot path), not asserting cloud-grade SLOs this
        # environment was never built to meet.
        assert summary["failure_rate"] == 0.0, f"Unexpected real failures: {summary['errors']}"
        assert summary["latency_ms"]["p95"] < 5000, f"p95 latency regression: {summary['latency_ms']}"

    def test_golden_authenticated_endpoints_under_light_concurrent_load(self, running_host):
        token = _get_real_admin_token()
        all_summaries = {}
        for scenario_key in ("candidates_list", "pipeline_config", "observability_summary"):
            results = perf_testing.run_load_scenario(running_host, scenario_key, num_requests=20, concurrency=5, auth_token=token)
            summary = perf_testing.summarize_run(results)
            all_summaries[scenario_key] = {"summary": summary, "concurrency": 5}
            assert summary["failure_rate"] == 0.0, f"{scenario_key}: unexpected real failures: {summary['errors']}"

        health_results = perf_testing.run_load_scenario(running_host, "health", num_requests=30, concurrency=10)
        all_summaries["health"] = {"summary": perf_testing.summarize_run(health_results), "concurrency": 10}

        _write_report(all_summaries)
