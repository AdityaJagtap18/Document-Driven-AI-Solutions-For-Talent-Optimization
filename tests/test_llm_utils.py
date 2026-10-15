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


class TestCallWithRetryObservability:
    """label=None (the default) is covered by every test above, which pass
    no label and must behave identically to before this feature existed.
    These cover the opt-in label= path, with get_openai_callback and
    observability.record_llm_call mocked out — no real API calls."""

    def test_golden_label_records_success(self, monkeypatch):
        recorded = []
        monkeypatch.setattr("observability.record_llm_call", lambda **kw: recorded.append(kw))

        class FakeCallback:
            prompt_tokens = 10
            completion_tokens = 5
            total_tokens = 15
            total_cost = 0.0002

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr("langchain_community.callbacks.get_openai_callback", lambda: FakeCallback())

        result = call_with_retry(lambda: "llm response", label="my_prompt", model="gpt-3.5-turbo", base_delay=0)

        assert result == "llm response"
        assert len(recorded) == 1
        assert recorded[0]["label"] == "my_prompt"
        assert recorded[0]["total_tokens"] == 15
        assert recorded[0]["success"] is True
        assert recorded[0]["retries"] == 0

    def test_golden_label_records_failure(self, monkeypatch):
        recorded = []
        monkeypatch.setattr("observability.record_llm_call", lambda **kw: recorded.append(kw))

        def always_fails():
            raise ValueError("boom")

        with pytest.raises(ValueError):
            call_with_retry(always_fails, label="my_prompt", max_attempts=2, base_delay=0)

        # 2 attempts, both fail -> 2 failure records, no success record
        assert len(recorded) == 2
        assert all(r["success"] is False for r in recorded)
        assert all(r["error"] == "boom" for r in recorded)

    def test_adversarial_observability_failure_does_not_break_the_llm_call(self, monkeypatch):
        def broken_record_llm_call(**kw):
            raise RuntimeError("observability backend down")

        monkeypatch.setattr("observability.record_llm_call", broken_record_llm_call)

        def always_fails():
            raise ValueError("the real error")

        # the LLM call's own ValueError must still propagate, not the
        # observability RuntimeError
        with pytest.raises(ValueError, match="the real error"):
            call_with_retry(always_fails, label="my_prompt", max_attempts=1, base_delay=0)
