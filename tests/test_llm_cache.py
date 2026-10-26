import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import llm_cache  # noqa: E402


class TestIsCacheableLabel:
    def test_golden_resume_chunk_summarization_is_cacheable(self):
        assert llm_cache.is_cacheable_label("resume_chunk_summarization") is True

    def test_golden_resume_merge_structured_extraction_is_cacheable(self):
        assert llm_cache.is_cacheable_label("resume_merge_structured_extraction") is True

    def test_adversarial_chat_candidate_qa_is_not_cacheable(self):
        assert llm_cache.is_cacheable_label("chat_candidate_qa") is False

    def test_adversarial_unknown_label_is_not_cacheable(self):
        assert llm_cache.is_cacheable_label("something_else") is False


class TestComputeCacheKey:
    def test_golden_identical_inputs_produce_identical_keys(self):
        k1 = llm_cache.compute_cache_key("resume_chunk_summarization", "gpt-4o-mini", "Resume text here.", 0, 500, "1")
        k2 = llm_cache.compute_cache_key("resume_chunk_summarization", "gpt-4o-mini", "Resume text here.", 0, 500, "1")
        assert k1 == k2

    def test_adversarial_different_prompt_text_produces_different_key(self):
        k1 = llm_cache.compute_cache_key("resume_chunk_summarization", "gpt-4o-mini", "Candidate A's resume.", 0, 500, "1")
        k2 = llm_cache.compute_cache_key("resume_chunk_summarization", "gpt-4o-mini", "Candidate B's resume.", 0, 500, "1")
        assert k1 != k2

    def test_adversarial_different_model_produces_different_key(self):
        k1 = llm_cache.compute_cache_key("resume_chunk_summarization", "gpt-4o-mini", "Resume text.", 0, 500, "1")
        k2 = llm_cache.compute_cache_key("resume_chunk_summarization", "gpt-4o", "Resume text.", 0, 500, "1")
        assert k1 != k2

    def test_adversarial_different_prompt_version_produces_different_key(self):
        k1 = llm_cache.compute_cache_key("resume_chunk_summarization", "gpt-4o-mini", "Resume text.", 0, 500, "1")
        k2 = llm_cache.compute_cache_key("resume_chunk_summarization", "gpt-4o-mini", "Resume text.", 0, 500, "2")
        assert k1 != k2

    def test_adversarial_different_temperature_produces_different_key(self):
        k1 = llm_cache.compute_cache_key("resume_chunk_summarization", "gpt-4o-mini", "Resume text.", 0, 500, "1")
        k2 = llm_cache.compute_cache_key("resume_chunk_summarization", "gpt-4o-mini", "Resume text.", 0.5, 500, "1")
        assert k1 != k2


class TestCacheGetSetClearStats:
    def test_golden_set_then_get_returns_the_cached_content(self, tmp_path):
        path = tmp_path / "cache.json"
        llm_cache.set("key-1", "cached summary text", "resume_chunk_summarization", path=path)
        assert llm_cache.get("key-1", path=path) == "cached summary text"

    def test_edge_miss_returns_none(self, tmp_path):
        path = tmp_path / "cache.json"
        assert llm_cache.get("nonexistent-key", path=path) is None

    def test_edge_missing_cache_file_is_a_cold_cache_not_an_error(self, tmp_path):
        path = tmp_path / "does_not_exist.json"
        assert llm_cache.get("any-key", path=path) is None
        assert llm_cache.stats(path=path)["entry_count"] == 0

    def test_adversarial_corrupt_cache_file_is_treated_as_empty(self, tmp_path):
        path = tmp_path / "corrupt.json"
        path.write_text("{not valid json", encoding="utf-8")
        assert llm_cache.get("any-key", path=path) is None

    def test_adversarial_expired_entry_is_a_miss(self, tmp_path):
        path = tmp_path / "cache.json"
        llm_cache.set("key-1", "stale content", "resume_chunk_summarization", path=path, now=1000.0)
        result = llm_cache.get("key-1", path=path, ttl_seconds=100, now=1000.0 + 101)
        assert result is None

    def test_golden_entry_within_ttl_is_a_hit(self, tmp_path):
        path = tmp_path / "cache.json"
        llm_cache.set("key-1", "fresh content", "resume_chunk_summarization", path=path, now=1000.0)
        result = llm_cache.get("key-1", path=path, ttl_seconds=100, now=1000.0 + 50)
        assert result == "fresh content"

    def test_golden_hit_count_increments_on_each_get(self, tmp_path):
        path = tmp_path / "cache.json"
        llm_cache.set("key-1", "content", "resume_chunk_summarization", path=path)
        llm_cache.get("key-1", path=path)
        llm_cache.get("key-1", path=path)
        stats = llm_cache.stats(path=path)
        assert stats["total_hit_count"] == 2

    def test_adversarial_max_entries_evicts_oldest_first(self, tmp_path):
        path = tmp_path / "cache.json"
        llm_cache.set("old", "old content", "resume_chunk_summarization", path=path, max_entries=2, now=1.0)
        llm_cache.set("mid", "mid content", "resume_chunk_summarization", path=path, max_entries=2, now=2.0)
        llm_cache.set("new", "new content", "resume_chunk_summarization", path=path, max_entries=2, now=3.0)
        assert llm_cache.get("old", path=path, now=3.0) is None
        assert llm_cache.get("mid", path=path, now=3.0) == "mid content"
        assert llm_cache.get("new", path=path, now=3.0) == "new content"

    def test_golden_clear_removes_every_entry_and_returns_the_count(self, tmp_path):
        path = tmp_path / "cache.json"
        llm_cache.set("key-1", "a", "resume_chunk_summarization", path=path)
        llm_cache.set("key-2", "b", "resume_merge_structured_extraction", path=path)
        removed = llm_cache.clear(path=path)
        assert removed == 2
        assert llm_cache.stats(path=path)["entry_count"] == 0

    def test_golden_stats_breaks_down_by_label(self, tmp_path):
        path = tmp_path / "cache.json"
        llm_cache.set("key-1", "a", "resume_chunk_summarization", path=path)
        llm_cache.set("key-2", "b", "resume_chunk_summarization", path=path)
        llm_cache.set("key-3", "c", "resume_merge_structured_extraction", path=path)
        llm_cache.get("key-1", path=path)
        stats = llm_cache.stats(path=path)
        assert stats["entry_count"] == 3
        assert stats["by_label"]["resume_chunk_summarization"]["entry_count"] == 2
        assert stats["by_label"]["resume_chunk_summarization"]["hit_count"] == 1
        assert stats["by_label"]["resume_merge_structured_extraction"]["entry_count"] == 1

    def test_edge_stats_on_empty_cache_reports_no_oldest_entry(self, tmp_path):
        path = tmp_path / "cache.json"
        stats = llm_cache.stats(path=path)
        assert stats["entry_count"] == 0
        assert stats["oldest_entry_age_seconds"] is None
