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


def _azure_openai_config():
    """Reads the five Azure OpenAI settings via secrets_loader (so Key
    Vault or .env both work, same as every other integration in this
    repo) and returns them only if ALL required fields are present --
    partial configuration is treated as "not configured," never a
    half-working Azure client. Returns None when not configured."""
    from secrets_loader import get_secret

    endpoint = get_secret("AZURE_OPENAI_ENDPOINT")
    api_key = get_secret("AZURE_OPENAI_API_KEY")
    chat_deployment = get_secret("AZURE_OPENAI_CHAT_DEPLOYMENT")
    if not (endpoint and api_key and chat_deployment):
        return None
    return {
        "endpoint": endpoint,
        "api_key": api_key,
        "api_version": get_secret("AZURE_OPENAI_API_VERSION") or "2024-10-21",
        "chat_deployment": chat_deployment,
        "embedding_deployment": get_secret("AZURE_OPENAI_EMBEDDING_DEPLOYMENT"),
    }


def using_azure_openai() -> bool:
    """True if this process will route chat/embedding calls to Azure
    OpenAI instead of plain OpenAI -- the single source of truth both
    get_chat_llm() and semantic-library/embeddings.py's provider
    selection use, so "is Azure actually selected" is never computed two
    different ways that could disagree."""
    return _azure_openai_config() is not None


def get_chat_llm(model: str = None, temperature: float = 0, max_tokens: int = None, **kwargs):
    """Drop-in replacement for constructing `ChatOpenAI(...)` directly --
    every real call site in this repo (ResumeParser.py, rag_preview.py,
    generate_questions.py, interview_bot.py, voicebot/voice_bot.py) now
    calls this instead. Returns a real `AzureChatOpenAI` (requests
    actually go to the configured Azure endpoint/deployment) when Azure
    OpenAI is fully configured (AZURE_OPENAI_ENDPOINT +
    AZURE_OPENAI_API_KEY + AZURE_OPENAI_CHAT_DEPLOYMENT all present),
    otherwise the existing plain `ChatOpenAI` -- fully backward
    compatible with every call site that passed `model=`, since Azure
    routes by `azure_deployment` (the admin-chosen deployment name,
    which may or may not match the underlying model's own name) rather
    than the `model` string rag_config.py's per-step config carries;
    `model` is still passed through for Azure too (used for tokenizer
    selection) but does not change which deployment is called."""
    azure_cfg = _azure_openai_config()
    if azure_cfg is not None:
        from langchain_openai import AzureChatOpenAI

        return AzureChatOpenAI(
            azure_endpoint=azure_cfg["endpoint"], api_key=azure_cfg["api_key"],
            api_version=azure_cfg["api_version"], azure_deployment=azure_cfg["chat_deployment"],
            model=model, temperature=temperature, max_tokens=max_tokens, **kwargs,
        )
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model=model, temperature=temperature, max_tokens=max_tokens, **kwargs)


def get_embedding_client_and_model(default_model: str):
    """Returns (client, model) for a plain `openai.embeddings.create(model=...)`
    call site (semantic-library/embeddings.py) -- `client` is a real
    `openai.AzureOpenAI` instance pointed at the configured endpoint/
    deployment when Azure OpenAI is configured, otherwise plain
    `openai.OpenAI()`. For Azure, `model` is the EMBEDDING deployment
    name (required separately from the chat deployment -- an admin can
    deploy different model families under each), substituted in place of
    `default_model` so the caller's `.embeddings.create(model=model, ...)`
    line doesn't need to know which provider it's talking to. Raises
    RuntimeError if Azure is otherwise configured but
    AZURE_OPENAI_EMBEDDING_DEPLOYMENT specifically is missing -- silently
    falling back to the chat deployment (a different model family) would
    produce garbage embeddings, worse than failing loudly."""
    azure_cfg = _azure_openai_config()
    if azure_cfg is not None:
        if not azure_cfg["embedding_deployment"]:
            raise RuntimeError(
                "Azure OpenAI is configured for chat (AZURE_OPENAI_CHAT_DEPLOYMENT) but "
                "AZURE_OPENAI_EMBEDDING_DEPLOYMENT is not set -- required separately since "
                "it's typically a different deployed model."
            )
        from openai import AzureOpenAI

        client = AzureOpenAI(
            azure_endpoint=azure_cfg["endpoint"], api_key=azure_cfg["api_key"], api_version=azure_cfg["api_version"],
        )
        return client, azure_cfg["embedding_deployment"]
    from openai import OpenAI

    return OpenAI(), default_model


def run_azure_openai_test_request() -> dict:
    """Explicit, admin-triggered real verification: sends ONE minimal
    chat completion and ONE minimal embedding request to the configured
    Azure OpenAI endpoint. NEVER called automatically by any readiness
    scan or health check -- this is a real, billable Azure API call
    (see azure_integrations.py's azure_ai_foundry adapter entry,
    capabilities.health_check: False, for why), only run when an admin
    explicitly asks for it. Returns
    {"configured": bool, "chat": {...}|None, "embedding": {...}|None},
    each sub-result real ("succeeded"/"failed" + a truncated, secret-free
    error or a short response preview) -- never a fabricated success."""
    azure_cfg = _azure_openai_config()
    if azure_cfg is None:
        return {
            "configured": False, "chat": None, "embedding": None,
            "detail": "Azure OpenAI is not configured (requires AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_CHAT_DEPLOYMENT).",
        }

    result = {"configured": True, "chat": None, "embedding": None}
    try:
        llm = get_chat_llm(temperature=0, max_tokens=5)
        response = llm.invoke("Reply with the single word: OK")
        result["chat"] = {"outcome": "succeeded", "response_preview": (getattr(response, "content", "") or "")[:50]}
    except Exception as e:
        result["chat"] = {"outcome": "failed", "error": str(e)[:500]}

    if azure_cfg["embedding_deployment"]:
        try:
            client, model = get_embedding_client_and_model("text-embedding-3-small")
            resp = client.embeddings.create(model=model, input=["connectivity test"])
            result["embedding"] = {"outcome": "succeeded", "dimensions": len(resp.data[0].embedding)}
        except Exception as e:
            result["embedding"] = {"outcome": "failed", "error": str(e)[:500]}
    else:
        result["embedding"] = {"outcome": "skipped", "detail": "AZURE_OPENAI_EMBEDDING_DEPLOYMENT not configured."}

    return result


def call_with_retry(fn, *args, max_attempts=3, base_delay=1.0, on_retry=None,
                     label=None, model=None, trace_id=None, parent_span_id=None,
                     correlation_id=None, prompt_version=None, workflow="llm", **kwargs):
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

    trace_id/parent_span_id/correlation_id let this one LLM call join a
    larger multi-stage AI Observability Center trace (e.g. ResumeParser.py
    passes the same trace_id into every stage of one resume's parse, so
    this call's span appears alongside document-extraction/chunking/
    embedding spans in the same trace rather than as an unrelated event).
    All optional, all None by default -- no effect on a call site that
    doesn't pass them. prompt_version is recorded on the span for the
    prompt/model version tracking the AI Observability Center surfaces.
    """
    last_exception = None
    for attempt in range(1, max_attempts + 1):
        start = time.perf_counter() if label else None
        try:
            if label:
                result = _call_with_observability(
                    fn, args, kwargs, label, model, start, attempt,
                    trace_id, parent_span_id, correlation_id, prompt_version, workflow,
                )
            else:
                result = fn(*args, **kwargs)
            return result
        except Exception as e:
            last_exception = e
            if label:
                _record_failure(label, model, start, e, attempt, trace_id, parent_span_id, correlation_id, prompt_version, workflow)
            if attempt == max_attempts:
                break
            if on_retry:
                on_retry(attempt, e)
            time.sleep(base_delay * (2 ** (attempt - 1)))
    raise last_exception


def cached_call_with_retry(fn, prompt_text, *, label, model, temperature=0, max_tokens=None,
                            prompt_version=None, **kwargs):
    """F-18 (LLM caching, minimal slice): a content-addressed cache
    wrapped around call_with_retry(), for the narrow set of call sites
    llm_cache.CACHEABLE_LABELS actually covers -- see that module's
    docstring for exactly why only those two are safe to cache here.

    Assumes this repo's established call shape for those two sites:
    `fn` is called as `fn(prompt_text)` (e.g. `llm.predict`), and
    returns a plain string. Not a generic replacement for
    call_with_retry() -- other call sites keep calling that directly.

    On a cache hit, the real model is never called: returns the cached
    string immediately and records a zero-cost observability entry
    (cache_hit=True) so the AI Observability Center and tokenomics still
    see that this request happened, just not as a billed call. On a
    miss, delegates to call_with_retry() exactly as before, then stores
    the real result for next time."""
    import llm_cache
    from observability import record_llm_call

    if not llm_cache.is_cacheable_label(label):
        return call_with_retry(fn, prompt_text, label=label, model=model, prompt_version=prompt_version, **kwargs)

    key = llm_cache.compute_cache_key(label, model, prompt_text, temperature, max_tokens, prompt_version)
    cached_content = llm_cache.get(key)
    if cached_content is not None:
        try:
            record_llm_call(
                label=label, model=model, latency_ms=0.0,
                prompt_tokens=0, completion_tokens=0, total_tokens=0, cost_usd=0.0,
                success=True, retries=0, prompt_version=prompt_version,
                workflow=kwargs.get("workflow", "llm"), cache_hit=True,
                trace_id=kwargs.get("trace_id"), parent_span_id=kwargs.get("parent_span_id"),
                correlation_id=kwargs.get("correlation_id"),
            )
        except Exception as e:
            print(f"[WARN] Observability recording for a cache hit failed (the cached result is still returned): {e}")
        return cached_content

    result = call_with_retry(fn, prompt_text, label=label, model=model, prompt_version=prompt_version, **kwargs)
    try:
        llm_cache.set(key, result, label)
    except Exception as e:
        print(f"[WARN] Caching the real LLM response failed (the real result is still returned): {e}")
    return result


def _call_with_observability(fn, args, kwargs, label, model, start, attempt,
                              trace_id, parent_span_id, correlation_id, prompt_version, workflow):
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
        trace_id=trace_id, parent_span_id=parent_span_id, correlation_id=correlation_id,
        prompt_version=prompt_version, workflow=workflow,
    )
    return result


def _record_failure(label, model, start, exception, attempt, trace_id, parent_span_id, correlation_id, prompt_version, workflow):
    try:
        from observability import record_llm_call

        latency_ms = (time.perf_counter() - start) * 1000 if start else None
        record_llm_call(
            label=label, model=model, latency_ms=latency_ms,
            success=False, error=str(exception), retries=attempt - 1,
            trace_id=trace_id, parent_span_id=parent_span_id, correlation_id=correlation_id,
            prompt_version=prompt_version, workflow=workflow,
        )
    except Exception as obs_error:
        print(f"[WARN] Observability recording failed (LLM call's own error is unaffected): {obs_error}")
