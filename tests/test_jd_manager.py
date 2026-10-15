import pytest

from jd_manager import list_jds, embed_jds, score_candidate_against_jds


class TestListJds:
    def test_golden_reads_all_txt_files(self, tmp_path):
        (tmp_path / "ml_developer.txt").write_text("ML role", encoding="utf-8")
        (tmp_path / "data_analyst.txt").write_text("DA role", encoding="utf-8")
        jds = list_jds(tmp_path)
        assert [jd["jd_id"] for jd in jds] == ["data_analyst", "ml_developer"]
        assert jds[0]["jd_filename"] == "data_analyst.txt"
        assert jds[0]["text"] == "DA role"

    def test_edge_missing_folder_returns_empty(self, tmp_path):
        assert list_jds(tmp_path / "does_not_exist") == []

    def test_edge_empty_folder_returns_empty(self, tmp_path):
        assert list_jds(tmp_path) == []

    def test_adversarial_blank_jd_file_skipped(self, tmp_path):
        (tmp_path / "blank.txt").write_text("   \n", encoding="utf-8")
        (tmp_path / "real.txt").write_text("Real JD", encoding="utf-8")
        jds = list_jds(tmp_path)
        assert [jd["jd_id"] for jd in jds] == ["real"]


class TestEmbedJds:
    def test_golden_adds_vector_per_jd(self):
        jds = [{"jd_id": "a", "jd_filename": "a.txt", "text": "foo"},
               {"jd_id": "b", "jd_filename": "b.txt", "text": "bar"}]

        def fake_embed_texts(texts):
            return [[len(t), 0.0] for t in texts]

        result = embed_jds(jds, fake_embed_texts)
        assert result[0]["vector"] == [3, 0.0]
        assert result[1]["vector"] == [3, 0.0]
        assert result[0]["jd_id"] == "a"  # original fields preserved

    def test_edge_empty_list_skips_embed_call(self):
        calls = []
        embed_jds([], lambda texts: calls.append(texts))
        assert calls == []  # embed_texts_fn never called for an empty list


class TestScoreCandidateAgainstJds:
    def test_golden_ranks_jds_by_score_descending(self):
        jds_with_vectors = [
            {"jd_id": "low", "jd_filename": "low.txt", "vector": [0.0]},
            {"jd_id": "high", "jd_filename": "high.txt", "vector": [1.0]},
        ]

        class FakeStore:
            def find_by_metadata(self, key, value):
                return [{"vector": [1.0]}]

        def fake_cosine_scores(vectors, query_vector):
            return [1.0 if query_vector == [1.0] else 0.2]

        result = score_candidate_against_jds("alice.pdf", jds_with_vectors, FakeStore(), fake_cosine_scores)
        assert [m["jd_id"] for m in result] == ["high", "low"]
        assert result[0]["score"] == 10.0
        assert result[1]["score"] == 2.0

    def test_edge_no_active_jds_returns_empty(self):
        class FakeStore:
            def find_by_metadata(self, key, value):
                return [{"vector": [1.0]}]

        result = score_candidate_against_jds("alice.pdf", [], FakeStore(), lambda v, q: [0.5])
        assert result == []

    def test_edge_resume_not_in_index_returns_empty(self):
        class FakeStore:
            def find_by_metadata(self, key, value):
                return []

        jds_with_vectors = [{"jd_id": "a", "jd_filename": "a.txt", "vector": [1.0]}]
        result = score_candidate_against_jds("missing.pdf", jds_with_vectors, FakeStore(), lambda v, q: [0.5])
        assert result == []

    def test_adversarial_negative_cosine_clamped_to_zero(self):
        class FakeStore:
            def find_by_metadata(self, key, value):
                return [{"vector": [1.0]}]

        jds_with_vectors = [{"jd_id": "a", "jd_filename": "a.txt", "vector": [1.0]}]
        result = score_candidate_against_jds("alice.pdf", jds_with_vectors, FakeStore(), lambda v, q: [-0.7])
        assert result[0]["score"] == 0.0
