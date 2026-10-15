import pytest

from llm_utils import call_with_retry


class TestCallWithRetry:
    def test_golden_succeeds_first_try_no_retry_needed(self):
        calls = []

        def fn():
            calls.append(1)
            return "ok"

        result = call_with_retry(fn, max_attempts=3, base_delay=0)
        assert result == "ok"
        assert len(calls) == 1

    def test_golden_succeeds_after_transient_failures(self):
        calls = []

        def fn():
            calls.append(1)
            if len(calls) < 3:
                raise ValueError("transient")
            return "ok"

        result = call_with_retry(fn, max_attempts=5, base_delay=0)
        assert result == "ok"
        assert len(calls) == 3

    def test_adversarial_exhausts_all_attempts_reraises(self):
        calls = []

        def fn():
            calls.append(1)
            raise ValueError("permanent failure")

        with pytest.raises(ValueError, match="permanent failure"):
            call_with_retry(fn, max_attempts=3, base_delay=0)
        assert len(calls) == 3

    def test_golden_passes_args_and_kwargs_through(self):
        def fn(a, b, c=None):
            return (a, b, c)

        result = call_with_retry(fn, 1, 2, c=3, max_attempts=2, base_delay=0)
        assert result == (1, 2, 3)

    def test_golden_on_retry_callback_invoked_per_retry_not_on_success(self):
        retry_log = []

        def fn():
            if len(retry_log) < 2:
                raise ValueError("retry me")
            return "ok"

        def on_retry(attempt, exc):
            retry_log.append((attempt, str(exc)))

        result = call_with_retry(fn, max_attempts=5, base_delay=0, on_retry=on_retry)
        assert result == "ok"
        assert retry_log == [(1, "retry me"), (2, "retry me")]

    def test_edge_on_retry_not_called_after_final_failed_attempt(self):
        retry_log = []

        def fn():
            raise ValueError("always fails")

        with pytest.raises(ValueError):
            call_with_retry(fn, max_attempts=2, base_delay=0, on_retry=lambda a, e: retry_log.append(a))
        # 2 attempts total -> exactly 1 retry happens before the final failure
        assert retry_log == [1]
