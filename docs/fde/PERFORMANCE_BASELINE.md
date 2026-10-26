# Performance Baseline (F-11, minimal slice)

Generated 2026-10-26T12:26:06.147607+00:00 by `tests/perf/test_load_baseline.py` against the real local Azure Functions host (`http://127.0.0.1:7071`). Every figure below is a real measured HTTP round-trip -- never fabricated or assumed. Scope: see `perf_testing.py`'s module docstring for exactly which endpoints are load-tested (an explicit, AI-call-free safety allowlist) and why.

| Scenario | Requests | Concurrency | Succeeded | Failed | Failure Rate | p50 (ms) | p95 (ms) | p99 (ms) | Throughput (req/s) |
|---|---|---|---|---|---|---|---|---|---|
| candidates_list | 20 | 5 | 20 | 0 | 0.0 | 3214.7 | 4181.3 | 4293.4 | 1.46 |
| pipeline_config | 20 | 5 | 20 | 0 | 0.0 | 2568.6 | 2684.7 | 2815.4 | 1.91 |
| observability_summary | 20 | 5 | 20 | 0 | 0.0 | 1993.4 | 2090.2 | 2125.3 | 2.48 |
| health | 30 | 10 | 30 | 0 | 0.0 | 11.8 | 39.5 | 42.3 | 461.6 |
