import math

import pytest

from vector_store import LocalVectorStore, cosine_scores


class TestCosineScores:
    def test_golden_identical_vectors(self):
        scores = cosine_scores([[1.0, 2.0, 3.0]], [1.0, 2.0, 3.0])
        assert scores[0] == pytest.approx(1.0, abs=1e-5)

    def test_golden_orthogonal_vectors(self):
        scores = cosine_scores([[1.0, 0.0]], [0.0, 1.0])
        assert scores[0] == pytest.approx(0.0, abs=1e-5)

    def test_golden_opposite_vectors(self):
        scores = cosine_scores([[1.0, 0.0]], [-1.0, 0.0])
        assert scores[0] == pytest.approx(-1.0, abs=1e-5)

    def test_golden_multiple_vectors_ranked_correctly(self):
        vectors = [[1.0, 0.0], [0.7, 0.7], [0.0, 1.0]]
        scores = cosine_scores(vectors, [1.0, 0.0])
        assert scores[0] > scores[1] > scores[2]


class TestLocalVectorStore:
    def test_golden_add_and_search(self, tmp_path):
        store = LocalVectorStore(str(tmp_path / "index.json"))
        store.add("doc1", [1.0, 0.0], {"label": "a"})
        store.add("doc2", [0.0, 1.0], {"label": "b"})
        results = store.search([1.0, 0.0], top_k=2)
        assert results[0]["id"] == "doc1"
        assert results[0]["metadata"]["label"] == "a"

    def test_edge_empty_store_search(self, tmp_path):
        store = LocalVectorStore(str(tmp_path / "index.json"))
        assert store.search([1.0, 0.0], top_k=5) == []

    def test_golden_add_same_id_updates_not_duplicates(self, tmp_path):
        store = LocalVectorStore(str(tmp_path / "index.json"))
        store.add("doc1", [1.0, 0.0], {"v": 1})
        store.add("doc1", [0.0, 1.0], {"v": 2})
        assert len(store) == 1
        results = store.search([0.0, 1.0], top_k=1)
        assert results[0]["metadata"]["v"] == 2

    def test_golden_save_and_load_roundtrip(self, tmp_path):
        path = str(tmp_path / "index.json")
        store = LocalVectorStore(path)
        store.add("doc1", [1.0, 2.0, 3.0], {"label": "a"})
        store.save()

        reloaded = LocalVectorStore(path)
        assert len(reloaded) == 1
        results = reloaded.search([1.0, 2.0, 3.0], top_k=1)
        assert results[0]["id"] == "doc1"
        assert results[0]["metadata"]["label"] == "a"

    def test_golden_find_by_metadata(self, tmp_path):
        store = LocalVectorStore(str(tmp_path / "index.json"))
        store.add("doc1", [1.0, 0.0], {"resume_name": "a.pdf"})
        store.add("doc2", [0.0, 1.0], {"resume_name": "a.pdf"})
        store.add("doc3", [1.0, 1.0], {"resume_name": "b.pdf"})
        matches = store.find_by_metadata("resume_name", "a.pdf")
        assert len(matches) == 2
        assert {m["id"] for m in matches} == {"doc1", "doc2"}

    def test_edge_find_by_metadata_no_match(self, tmp_path):
        store = LocalVectorStore(str(tmp_path / "index.json"))
        store.add("doc1", [1.0, 0.0], {"resume_name": "a.pdf"})
        assert store.find_by_metadata("resume_name", "nonexistent.pdf") == []
