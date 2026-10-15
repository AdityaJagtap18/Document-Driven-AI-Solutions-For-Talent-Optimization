"""Tests for azure-functions/api_blueprint.py. These call the route
functions directly (the same technique used to manually verify this file
before committing it) — no running Functions host needed, no real Cosmos
DB needed. repositories.* is monkeypatched per test so these never touch
a network."""

import json

import azure.functions as func
import pytest

import api_blueprint as api
import repositories


def _req(method="GET", route_params=None, body=None):
    return func.HttpRequest(
        method=method,
        url="/api/test",
        body=json.dumps(body).encode() if body is not None else None,
        route_params=route_params or {},
    )


class TestHealth:
    def test_golden_returns_ok(self):
        resp = api.health(_req())
        assert resp.status_code == 200
        assert json.loads(resp.get_body()) == {"status": "ok"}


class TestListCandidates:
    def test_golden_returns_candidates(self, monkeypatch):
        monkeypatch.setattr(repositories, "list_candidates", lambda ranking_tier=None: [{"candidate_id": "1"}])
        resp = api.list_candidates_endpoint(_req())
        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["count"] == 1

    def test_adversarial_cosmos_unavailable_returns_503(self, monkeypatch):
        def broken(ranking_tier=None):
            raise RuntimeError("Missing required secret(s): COSMOS_ENDPOINT")

        monkeypatch.setattr(repositories, "list_candidates", broken)
        resp = api.list_candidates_endpoint(_req())
        assert resp.status_code == 503
        assert "COSMOS_ENDPOINT" in json.loads(resp.get_body())["error"]


class TestGetCandidate:
    def test_golden_found(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_candidate", lambda cid: {"candidate_id": cid, "Name": "Test"})
        resp = api.get_candidate_endpoint(_req(route_params={"candidate_id": "abc"}))
        assert resp.status_code == 200
        assert json.loads(resp.get_body())["Name"] == "Test"

    def test_edge_not_found_returns_404_not_503(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_candidate", lambda cid: None)
        resp = api.get_candidate_endpoint(_req(route_params={"candidate_id": "abc"}))
        assert resp.status_code == 404

    def test_adversarial_cosmos_unavailable_returns_503_not_404(self, monkeypatch):
        def broken(cid):
            raise RuntimeError("Missing required secret(s): COSMOS_KEY")

        monkeypatch.setattr(repositories, "get_candidate", broken)
        resp = api.get_candidate_endpoint(_req(route_params={"candidate_id": "abc"}))
        # the whole point of the repositories.py fix this session: a
        # misconfigured backend must not look identical to "not found"
        assert resp.status_code == 503


class TestCandidateByEmail:
    def test_golden_returns_only_safe_fields(self, monkeypatch):
        monkeypatch.setattr(
            repositories, "find_candidate_by_email",
            lambda email: {
                "Name": "Jane Doe", "resume_name": "jane.pdf", "ranking_tier": "To be Considered",
                "ingested_at": "2026-01-01", "HR Notes": "confidential", "Red Flags & Risk Analysis": ["secret"],
            },
        )
        resp = api.get_candidate_by_email_endpoint(_req(route_params={"email": "jane@example.com"}))
        body = json.loads(resp.get_body())
        assert body == {
            "Name": "Jane Doe", "resume_name": "jane.pdf",
            "ranking_tier": "To be Considered", "ingested_at": "2026-01-01",
        }
        assert "HR Notes" not in body
        assert "Red Flags & Risk Analysis" not in body

    def test_edge_not_found(self, monkeypatch):
        monkeypatch.setattr(repositories, "find_candidate_by_email", lambda email: None)
        resp = api.get_candidate_by_email_endpoint(_req(route_params={"email": "nobody@example.com"}))
        assert resp.status_code == 404


class TestReviewCandidate:
    def test_golden_approve_records_audit_log(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_candidate", lambda cid: {"candidate_id": cid, "ranking_tier": "To be Considered"})
        upserted = {}
        monkeypatch.setattr(repositories, "upsert_candidate", lambda c: upserted.update(c))
        audit = {}
        monkeypatch.setattr(repositories, "append_audit_log", lambda e: audit.update(e))

        resp = api.review_candidate_endpoint(_req(
            method="POST", route_params={"candidate_id": "abc"},
            body={"decision": "approve", "reviewer": "recruiter@example.com"},
        ))

        assert resp.status_code == 200
        assert upserted["ranking_tier"] == "Recommended for Fast-Track"
        assert audit["action"] == "human_review_approve"
        assert audit["reviewer"] == "recruiter@example.com"

    def test_adversarial_invalid_decision_rejected(self):
        resp = api.review_candidate_endpoint(_req(
            method="POST", route_params={"candidate_id": "abc"},
            body={"decision": "maybe", "reviewer": "x"},
        ))
        assert resp.status_code == 400

    def test_adversarial_missing_reviewer_rejected(self):
        resp = api.review_candidate_endpoint(_req(
            method="POST", route_params={"candidate_id": "abc"},
            body={"decision": "approve"},
        ))
        assert resp.status_code == 400

    def test_edge_candidate_not_found(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_candidate", lambda cid: None)
        resp = api.review_candidate_endpoint(_req(
            method="POST", route_params={"candidate_id": "missing"},
            body={"decision": "reject", "reviewer": "x"},
        ))
        assert resp.status_code == 404

    def test_adversarial_non_json_body(self):
        req = func.HttpRequest(method="POST", url="/api/test", body=b"not json", route_params={"candidate_id": "abc"})
        resp = api.review_candidate_endpoint(req)
        assert resp.status_code == 400


class TestObservabilitySummary:
    def test_golden_aggregates_real_log_file(self, tmp_path, monkeypatch):
        log_path = tmp_path / "llm_observability.jsonl"
        log_path.write_text(
            json.dumps({"label": "a", "total_tokens": 10, "cost_usd": 0.001}) + "\n"
            + json.dumps({"label": "a", "total_tokens": 5, "cost_usd": 0.0005}) + "\n"
            + json.dumps({"label": "b", "total_tokens": 20, "cost_usd": 0.002}) + "\n",
            encoding="utf-8",
        )
        import observability
        monkeypatch.setattr(observability, "LOG_PATH", log_path)

        resp = api.observability_summary_endpoint(_req())
        body = json.loads(resp.get_body())
        assert body["total_calls"] == 3
        assert body["total_tokens"] == 35
        assert body["by_label"]["a"]["calls"] == 2
        assert body["by_label"]["b"]["calls"] == 1

    def test_edge_no_log_file_returns_zeros(self, tmp_path, monkeypatch):
        import observability
        monkeypatch.setattr(observability, "LOG_PATH", tmp_path / "does_not_exist.jsonl")

        resp = api.observability_summary_endpoint(_req())
        body = json.loads(resp.get_body())
        assert body == {"total_calls": 0, "total_tokens": 0, "total_cost_usd": 0.0, "by_label": {}}
