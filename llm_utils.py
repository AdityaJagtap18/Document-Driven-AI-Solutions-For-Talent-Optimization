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


def call_with_retry(fn, *args, max_attempts=3, base_delay=1.0, on_retry=None, **kwargs):
    """Calls fn(*args, **kwargs), retrying on any exception up to
    max_attempts times total, with exponential backoff between attempts
    (base_delay, base_delay*2, base_delay*4, ...). Re-raises the last
    exception if every attempt fails. `on_retry(attempt, exception)`, if
    given, is called before each retry's sleep (e.g. for logging) — it is
    NOT called after the final failed attempt, since there's no retry then.
    """
    last_exception = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_exception = e
            if attempt == max_attempts:
                break
            if on_retry:
                on_retry(attempt, e)
            time.sleep(base_delay * (2 ** (attempt - 1)))
    raise last_exception
