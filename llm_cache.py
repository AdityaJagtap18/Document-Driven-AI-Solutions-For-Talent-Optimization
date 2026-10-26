"""F-18 (intelligent LLM caching and response optimization) -- minimal slice.

Scope note (read before extending this file): the full F-18 feature
prompt describes exact-match + semantic + provider-native caching across
every AI surface in CDPI (candidate chatbots, interviewer chatbots,
recruitment FAQs, resume processing, interview question generation),
with configurable similarity thresholds, permission-scoped cache keys,
and cross-user leakage prevention. This slice implements exactly the
one real, safe, high-value case this repository's own call shape
supports today:

  - EXACT-MATCH caching only (no semantic/similarity matching -- that
    needs an embedding model + similarity threshold this slice does not
    build; deferred, not silently skipped).
  - Applied ONLY to ResumeParser.py's two deterministic, structured-
    extraction LLM calls (resume_chunk_summarization,
    resume_merge_structured_extraction) -- see CACHEABLE_LABELS. These
    are the one real case in this repo where (a) the exact same input
    (a given resume chunk/JD pair) genuinely recurs (reprocessing on
    retry, re-ingesting a demo resume, running the same file through the
    pipeline twice) and (b) the cached response is written back only
    into THAT SAME candidate's own record, never served to a different
    user's request. Candidate chatbots (F-07/F-09), interview-question
    generation, and the LLM-as-judge evaluators (F-19/F-21) are
    deliberately NOT cached in this slice -- they're either genuinely
    conversational/non-deterministic, or evaluation-integrity-sensitive
    (a cached judge score would defeat the point of re-running an
    evaluation). See docs/fde/IMPLEMENTATION_STATUS.md.
  - CROSS-CANDIDATE LEAKAGE PREVENTION BY CONSTRUCTION, not a separate
    check: the cache key includes the full prompt text, which already
    contains the candidate's own resume content -- two different
    candidates' resumes produce different keys automatically. A cache
    hit can only ever occur for a byte-identical (label, model, prompt
    text, temperature, max_tokens, prompt_version) tuple, which means
    the input truly is identical, so returning the same output is
    correct, not leakage.
  - SELF-INVALIDATION BY CONSTRUCTION: because the key is content-
    addressed (the resume/JD text is IN the key), a changed resume or
    JD automatically produces a different key -- there is no separate
    "invalidate when source data changes" step to get wrong. A fixed
    TTL (DEFAULT_TTL_SECONDS) is still enforced as a backstop against an
    unboundedly stale entry.
  - LOCAL, PROCESS-LOCAL STORAGE ONLY: a single JSON file
    (logs/llm_response_cache.json), matching this repo's existing
    logs/*.jsonl convention for local-first telemetry. NOT a distributed
    cache (no Redis/Cosmos) and NOT shared across multiple Function App
    instances -- real production use at scale would need a shared store;
    explicitly deferred here, not pretended away.
"""

import hashlib
import json
import time
from pathlib import Path

DEFAULT_CACHE_PATH = Path(__file__).resolve().parent / "logs" / "llm_response_cache.json"
DEFAULT_TTL_SECONDS = 30 * 24 * 3600  # 30 days
DEFAULT_MAX_ENTRIES = 500

# The only two real call sites this slice caches -- see module docstring.
CACHEABLE_LABELS = {"resume_chunk_summarization", "resume_merge_structured_extraction"}


def is_cacheable_label(label: str) -> bool:
    return label in CACHEABLE_LABELS


def compute_cache_key(label: str, model: str, prompt_text: str, temperature, max_tokens, prompt_version: str) -> str:
    """Deterministic key over every input that can change the real
    output -- label, model, the full prompt text (which already embeds
    the candidate-specific resume/JD content), temperature, max_tokens,
    and prompt template version. Changing any one of these is a
    genuinely different request and must miss the cache."""
    canonical = json.dumps(
        {
            "label": label, "model": model, "prompt_text": prompt_text,
            "temperature": temperature, "max_tokens": max_tokens, "prompt_version": prompt_version,
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _load(path: Path = None) -> dict:
    path = path or DEFAULT_CACHE_PATH
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        # A corrupt cache file must never crash the real pipeline -- treat
        # it as empty (equivalent to a cold cache), not a fatal error.
        return {}


def _save(data: dict, path: Path = None) -> None:
    path = path or DEFAULT_CACHE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def get(key: str, path: Path = None, ttl_seconds: int = None, now: float = None) -> str:
    """Returns the cached content string, or None on a miss/expired
    entry. A hit increments the entry's hit_count and updates
    last_hit_at (for stats()), persisted immediately."""
    ttl_seconds = DEFAULT_TTL_SECONDS if ttl_seconds is None else ttl_seconds
    now = time.time() if now is None else now
    data = _load(path)
    entry = data.get(key)
    if entry is None:
        return None
    if now - entry["created_at"] > ttl_seconds:
        return None
    entry["hit_count"] = entry.get("hit_count", 0) + 1
    entry["last_hit_at"] = now
    _save(data, path)
    return entry["content"]


def set(key: str, content: str, label: str, path: Path = None, max_entries: int = None, now: float = None) -> None:
    """Stores a new entry, evicting the oldest entries first if this
    would exceed max_entries -- a simple, real bound on unbounded local
    disk growth, not a sophisticated LRU (unnecessary at this scale)."""
    max_entries = DEFAULT_MAX_ENTRIES if max_entries is None else max_entries
    now = time.time() if now is None else now
    data = _load(path)
    data[key] = {"content": content, "label": label, "created_at": now, "hit_count": 0, "last_hit_at": None}
    if len(data) > max_entries:
        oldest_first = sorted(data.items(), key=lambda kv: kv[1]["created_at"])
        for stale_key, _ in oldest_first[: len(data) - max_entries]:
            del data[stale_key]
    _save(data, path)


def clear(path: Path = None) -> int:
    """Removes every cache entry. Returns the number of entries removed."""
    data = _load(path)
    count = len(data)
    _save({}, path)
    return count


def stats(path: Path = None, now: float = None) -> dict:
    """Real counts only -- never fabricates a hit rate or savings figure
    the real cache data doesn't support. Estimated cost/token savings are
    computed by the caller (api_blueprint.py), which has access to real
    historical per-label cost data from logs/llm_observability.jsonl --
    this module only knows about the cache itself."""
    now = time.time() if now is None else now
    data = _load(path)
    by_label = {}
    total_hits = 0
    for entry in data.values():
        label = entry.get("label", "unknown")
        label_stats = by_label.setdefault(label, {"entry_count": 0, "hit_count": 0})
        label_stats["entry_count"] += 1
        label_stats["hit_count"] += entry.get("hit_count", 0)
        total_hits += entry.get("hit_count", 0)
    created_ats = [e["created_at"] for e in data.values()]
    return {
        "entry_count": len(data),
        "total_hit_count": total_hits,
        "by_label": by_label,
        "oldest_entry_age_seconds": round(now - min(created_ats), 1) if created_ats else None,
    }
