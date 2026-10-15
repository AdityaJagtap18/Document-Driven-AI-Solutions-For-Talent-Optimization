"""Shared retry/backoff wrapper for LLM API calls, used across ResumeParser.py,
generate_questions.py, interview_bot.py, and voicebot/voice_bot.py — covers
all 8 LLM call sites catalogued in prompts/registry.json.

Hand-rolled rather than a new dependency (e.g. tenacity): the retry need
here is simple (a few attempts, exponential backoff, retry on anything),
and the exceptions raised by the langchain/openai versions in use across
these scripts aren't uniformly typed, so a broad `except Exception` is
actually the right scope here, not a reason to reach for a heavier library.
"""

import time


def call_with_retry(fn, *args, max_attempts=3, base_delay=1.0, on_retry=None,
                     label=None, model=None, **kwargs):
    """Calls fn(*args, **kwargs), retrying on any exception up to
    max_attempts times total, with exponential backoff between attempts
    (base_delay, base_delay*2, base_delay*4, ...). Re-raises the last
    exception if every attempt fails. `on_retry(attempt, exception)`, if
    given, is called before each retry's sleep (e.g. for logging) — it is
    NOT called after the final failed attempt, since there's no retry then.

    If `label` is given, also records latency/token usage/cost/success via
    observability.record_llm_call() (logs locally always, Application
    Insights too if configured) — wraps the call with LangChain's
    get_openai_callback() to capture token counts, which works for any
    OpenAI-backed LangChain call (ChatOpenAI.predict(), calling the llm
    instance directly, etc.) regardless of call style. Leaving `label`
    unset (the default) skips all of this — fully backward compatible with
    every existing call site that doesn't pass it.
    """
    last_exception = None
    for attempt in range(1, max_attempts + 1):
        start = time.perf_counter() if label else None
        try:
            if label:
                result = _call_with_observability(fn, args, kwargs, label, model, start, attempt)
            else:
                result = fn(*args, **kwargs)
            return result
        except Exception as e:
            last_exception = e
            if label:
                _record_failure(label, model, start, e, attempt)
            if attempt == max_attempts:
                break
            if on_retry:
                on_retry(attempt, e)
            time.sleep(base_delay * (2 ** (attempt - 1)))
    raise last_exception


def _call_with_observability(fn, args, kwargs, label, model, start, attempt):
    from langchain_community.callbacks import get_openai_callback
    from observability import record_llm_call

    with get_openai_callback() as cb:
        result = fn(*args, **kwargs)
    latency_ms = (time.perf_counter() - start) * 1000
    record_llm_call(
        label=label, model=model, latency_ms=latency_ms,
        prompt_tokens=cb.prompt_tokens, completion_tokens=cb.completion_tokens,
        total_tokens=cb.total_tokens, cost_usd=cb.total_cost,
        success=True, retries=attempt - 1,
    )
    return result


def _record_failure(label, model, start, exception, attempt):
    try:
        from observability import record_llm_call

        latency_ms = (time.perf_counter() - start) * 1000 if start else None
        record_llm_call(
            label=label, model=model, latency_ms=latency_ms,
            success=False, error=str(exception), retries=attempt - 1,
        )
    except Exception as obs_error:
        print(f"[WARN] Observability recording failed (LLM call's own error is unaffected): {obs_error}")
