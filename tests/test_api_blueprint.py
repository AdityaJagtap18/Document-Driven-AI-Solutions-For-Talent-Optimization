"""Tests for azure-functions/api_blueprint.py. These call the route
functions directly (the same technique used to manually verify this file
before committing it) — no running Functions host needed, no real Cosmos
DB needed. repositories.* is monkeypatched per test so these never touch
a network."""

import base64
import json

import azure.functions as func
import pytest

import api_blueprint as api
import repositories
import jd_manager
import blob_store


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


class TestExecutiveSummary:
    def test_golden_computes_tier_distribution_and_approval_rate(self, monkeypatch):
        monkeypatch.setattr(
            repositories, "list_candidates",
            lambda ranking_tier=None: [
                {"ranking_tier": "Recommended for Fast-Track"},
                {"ranking_tier": "Recommended for Fast-Track"},
                {"ranking_tier": "Rejected"},
                {"ranking_tier": "To be Considered"},
            ],
        )
        monkeypatch.setattr(
            repositories, "list_all_audit_log",
            lambda action=None: [
                {"action": "human_review_approve"},
                {"action": "human_review_approve"},
                {"action": "human_review_reject"},
                {"action": "document_verification"},  # not a review decision, excluded from the rate
            ],
        )
        resp = api.executive_summary_endpoint(_req())
        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["total_candidates"] == 4
        assert body["candidates_by_tier"] == {
            "Recommended for Fast-Track": 2, "Rejected": 1, "To be Considered": 1,
        }
        assert body["human_review"] == {
            "approvals": 2, "rejections": 1, "total_reviewed": 3, "approval_rate": round(2 / 3, 4),
        }

    def test_edge_no_candidates_or_reviews_yet(self, monkeypatch):
        monkeypatch.setattr(repositories, "list_candidates", lambda ranking_tier=None: [])
        monkeypatch.setattr(repositories, "list_all_audit_log", lambda action=None: [])
        resp = api.executive_summary_endpoint(_req())
        body = json.loads(resp.get_body())
        assert body["total_candidates"] == 0
        assert body["candidates_by_tier"] == {}
        assert body["human_review"]["approval_rate"] is None  # not 0 -- "no data" is not "0% approval"

    def test_edge_candidate_missing_ranking_tier_counts_as_unscored(self, monkeypatch):
        monkeypatch.setattr(repositories, "list_candidates", lambda ranking_tier=None: [{"candidate_id": "1"}])
        monkeypatch.setattr(repositories, "list_all_audit_log", lambda action=None: [])
        resp = api.executive_summary_endpoint(_req())
        body = json.loads(resp.get_body())
        assert body["candidates_by_tier"] == {"Unscored": 1}

    def test_adversarial_cosmos_unavailable_returns_503(self, monkeypatch):
        def broken(ranking_tier=None):
            raise RuntimeError("Missing required secret(s): COSMOS_ENDPOINT")

        monkeypatch.setattr(repositories, "list_candidates", broken)
        resp = api.executive_summary_endpoint(_req())
        assert resp.status_code == 503


class TestListJds:
    def test_golden_returns_jd_ids_and_filenames(self, monkeypatch):
        monkeypatch.setattr(
            jd_manager, "list_jds",
            lambda jd_folder: [
                {"jd_id": "ml_developer", "jd_filename": "ml_developer.txt", "path": "x", "text": "y"},
            ],
        )
        resp = api.list_jds_endpoint(_req())
        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body == {"count": 1, "jds": [{"jd_id": "ml_developer", "jd_filename": "ml_developer.txt"}]}

    def test_edge_no_active_jds_returns_empty_list(self, monkeypatch):
        monkeypatch.setattr(jd_manager, "list_jds", lambda jd_folder: [])
        resp = api.list_jds_endpoint(_req())
        assert resp.status_code == 200
        assert json.loads(resp.get_body()) == {"count": 0, "jds": []}


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


class TestSubmitApplication:
    def _body(self, **overrides):
        base = {
            "full_name": "Jane Doe", "email": "jane@example.com", "phone": "5551234567",
            "resume_base64": base64.b64encode(b"%PDF-1.4 fake pdf bytes").decode(),
            "resume_filename": "jane_doe_resume.pdf",
        }
        base.update(overrides)
        return base

    def test_golden_uploads_and_logs_audit_entry(self, monkeypatch):
        uploaded = {}
        monkeypatch.setattr(
            blob_store, "upload_resume_bytes",
            lambda container, path, data: uploaded.update(container=container, path=path, data=data),
        )
        logged = {}
        monkeypatch.setattr(repositories, "append_audit_log", lambda entry: logged.update(entry))

        resp = api.submit_application_endpoint(_req(body=self._body()))
        assert resp.status_code == 202
        body = json.loads(resp.get_body())
        assert body["status"] == "submitted"
        assert "candidate_id" in body

        assert uploaded["container"] == "raw-resumes"
        assert uploaded["path"] == "jane_doe_resume.pdf"
        assert uploaded["data"] == b"%PDF-1.4 fake pdf bytes"

        assert logged["action"] == "application_submitted"
        assert logged["email"] == "jane@example.com"
        assert logged["candidate_id"] == body["candidate_id"]

    def test_golden_candidate_id_matches_real_pipeline_convention(self, monkeypatch):
        # candidate_id must be computed the exact same way ResumeParser.py's
        # dual-write will compute it later for the same filename, or status
        # lookups after processing would never find this candidate.
        from candidate_id import candidate_id_for

        monkeypatch.setattr(blob_store, "upload_resume_bytes", lambda container, path, data: None)
        monkeypatch.setattr(repositories, "append_audit_log", lambda entry: entry)

        resp = api.submit_application_endpoint(_req(body=self._body(resume_filename="unique_name_123.pdf")))
        body = json.loads(resp.get_body())
        assert body["candidate_id"] == candidate_id_for("unique_name_123.pdf")

    def test_edge_missing_full_name_returns_400(self, monkeypatch):
        resp = api.submit_application_endpoint(_req(body=self._body(full_name="")))
        assert resp.status_code == 400

    def test_edge_missing_email_returns_400(self, monkeypatch):
        resp = api.submit_application_endpoint(_req(body=self._body(email="")))
        assert resp.status_code == 400

    def test_edge_missing_resume_returns_400(self, monkeypatch):
        resp = api.submit_application_endpoint(_req(body=self._body(resume_base64="")))
        assert resp.status_code == 400

    def test_edge_non_pdf_filename_rejected(self, monkeypatch):
        resp = api.submit_application_endpoint(_req(body=self._body(resume_filename="resume.docx")))
        assert resp.status_code == 400

    def test_adversarial_invalid_base64_returns_400(self, monkeypatch):
        resp = api.submit_application_endpoint(_req(body=self._body(resume_base64="not valid base64!!!")))
        assert resp.status_code == 400

    def test_adversarial_path_traversal_filename_sanitized(self, monkeypatch):
        uploaded = {}
        monkeypatch.setattr(
            blob_store, "upload_resume_bytes",
            lambda container, path, data: uploaded.update(path=path),
        )
        monkeypatch.setattr(repositories, "append_audit_log", lambda entry: entry)

        resp = api.submit_application_endpoint(
            _req(body=self._body(resume_filename="../../etc/passwd_evil.pdf"))
        )
        assert resp.status_code == 202
        assert uploaded["path"] == "passwd_evil.pdf"
        assert ".." not in uploaded["path"]

    def test_adversarial_blob_storage_unavailable_returns_503(self, monkeypatch):
        def broken(container, path, data):
            raise RuntimeError("Missing required secret(s): BLOB_CONNECTION_STRING")

        monkeypatch.setattr(blob_store, "upload_resume_bytes", broken)
        resp = api.submit_application_endpoint(_req(body=self._body()))
        assert resp.status_code == 503

    def test_adversarial_audit_log_failure_does_not_break_response(self, monkeypatch):
        monkeypatch.setattr(blob_store, "upload_resume_bytes", lambda container, path, data: None)

        def broken_audit(entry):
            raise RuntimeError("Missing required secret(s): COSMOS_ENDPOINT")

        monkeypatch.setattr(repositories, "append_audit_log", broken_audit)
        resp = api.submit_application_endpoint(_req(body=self._body()))
        # the upload already succeeded -- an audit-log failure must not be
        # reported back as if the application submission itself failed
        assert resp.status_code == 202


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
        # no Phone field on this candidate -> notification skipped, not attempted
        assert json.loads(resp.get_body())["whatsapp_notification"] == "skipped: no phone number on file"

    def test_golden_approve_sends_whatsapp_when_phone_present(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_candidate", lambda cid: {"candidate_id": cid, "Name": "Priya", "Phone": "15551234567"})
        monkeypatch.setattr(repositories, "upsert_candidate", lambda c: None)
        monkeypatch.setattr(repositories, "append_audit_log", lambda e: None)

        sent = {}

        def fake_send_template(to, template_name, parameters=None, **kw):
            sent["to"] = to
            sent["template_name"] = template_name
            sent["parameters"] = parameters
            return {"messages": [{"id": "wamid.fake"}]}

        import whatsapp_client
        monkeypatch.setattr(whatsapp_client, "send_whatsapp_template", fake_send_template)

        resp = api.review_candidate_endpoint(_req(
            method="POST", route_params={"candidate_id": "abc"},
            body={"decision": "approve", "reviewer": "recruiter@example.com"},
        ))

        body = json.loads(resp.get_body())
        assert resp.status_code == 200
        assert body["whatsapp_notification"] == "sent"
        assert sent["to"] == "15551234567"
        assert sent["parameters"][0] == "Priya"

    def test_adversarial_whatsapp_failure_does_not_break_the_review_response(self, monkeypatch):
        """The review was already recorded successfully — a WhatsApp
        failure must show up as a field in the 200 response, not turn the
        whole request into an error."""
        monkeypatch.setattr(repositories, "get_candidate", lambda cid: {"candidate_id": cid, "Phone": "15551234567"})
        monkeypatch.setattr(repositories, "upsert_candidate", lambda c: None)
        monkeypatch.setattr(repositories, "append_audit_log", lambda e: None)

        import whatsapp_client

        def broken_send(*a, **kw):
            raise RuntimeError("Missing required secret(s): WHATSAPP_ACCESS_TOKEN")

        monkeypatch.setattr(whatsapp_client, "send_whatsapp_template", broken_send)

        resp = api.review_candidate_endpoint(_req(
            method="POST", route_params={"candidate_id": "abc"},
            body={"decision": "reject", "reviewer": "recruiter@example.com"},
        ))

        body = json.loads(resp.get_body())
        assert resp.status_code == 200
        assert body["status"] == "recorded"
        assert "failed:" in body["whatsapp_notification"]

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


class TestVerifyDocument:
    def _body(self, **overrides):
        import base64
        defaults = {
            "document_base64": base64.b64encode(b"fake pdf bytes").decode(),
            "claimed_fields": {"Name": "Priya Sharma", "Degree": "B.Tech Computer Science"},
        }
        defaults.update(overrides)
        return defaults

    def test_golden_all_fields_match(self, monkeypatch):
        import document_intelligence_client
        monkeypatch.setattr(document_intelligence_client, "analyze_document", lambda file_bytes, **kw: {
            "Name": {"value": "Priya Sharma", "confidence": 0.98},
            "Degree": {"value": "B.Tech Computer Science", "confidence": 0.91},
        })
        audit_calls = []
        monkeypatch.setattr(repositories, "append_audit_log", lambda e: audit_calls.append(e))

        resp = api.verify_document_endpoint(_req(method="POST", route_params={"candidate_id": "abc"}, body=self._body()))
        body = json.loads(resp.get_body())

        assert resp.status_code == 200
        assert body["cross_check"]["match_rate"] == 1.0
        assert body["cross_check"]["mismatches"] == []
        assert body["confidence_flagged_fields"] == []
        assert body["summary"].startswith("OK:")
        assert audit_calls[0]["action"] == "document_verification"

    def test_golden_mismatch_detected(self, monkeypatch):
        import document_intelligence_client
        monkeypatch.setattr(document_intelligence_client, "analyze_document", lambda file_bytes, **kw: {
            "Name": {"value": "Priya Sharma", "confidence": 0.98},
            "Degree": {"value": "B.Sc Physics", "confidence": 0.91},  # doesn't match claimed
        })
        monkeypatch.setattr(repositories, "append_audit_log", lambda e: None)

        resp = api.verify_document_endpoint(_req(method="POST", route_params={"candidate_id": "abc"}, body=self._body()))
        body = json.loads(resp.get_body())

        assert resp.status_code == 200
        assert "Degree" in [m["field"] for m in body["cross_check"]["mismatches"]]
        assert body["summary"].startswith("REVIEW: field mismatch")

    def test_golden_low_confidence_flagged(self, monkeypatch):
        import document_intelligence_client
        monkeypatch.setattr(document_intelligence_client, "analyze_document", lambda file_bytes, **kw: {
            "Name": {"value": "Priya Sharma", "confidence": 0.98},
            "Degree": {"value": "B.Tech Computer Science", "confidence": 0.3},
        })
        monkeypatch.setattr(repositories, "append_audit_log", lambda e: None)

        resp = api.verify_document_endpoint(_req(method="POST", route_params={"candidate_id": "abc"}, body=self._body()))
        body = json.loads(resp.get_body())

        assert "Degree" in body["confidence_flagged_fields"]
        assert "low OCR confidence" in body["summary"]

    def test_adversarial_missing_document(self):
        resp = api.verify_document_endpoint(_req(
            method="POST", route_params={"candidate_id": "abc"},
            body={"claimed_fields": {"Name": "Priya"}},
        ))
        assert resp.status_code == 400

    def test_adversarial_missing_claimed_fields(self):
        resp = api.verify_document_endpoint(_req(
            method="POST", route_params={"candidate_id": "abc"},
            body=self._body(claimed_fields={}),
        ))
        assert resp.status_code == 400

    def test_adversarial_invalid_base64(self):
        resp = api.verify_document_endpoint(_req(
            method="POST", route_params={"candidate_id": "abc"},
            body=self._body(document_base64="not valid base64!!!"),
        ))
        assert resp.status_code == 400

    def test_adversarial_document_intelligence_unavailable_returns_503(self, monkeypatch):
        import document_intelligence_client

        def broken(file_bytes, **kw):
            raise RuntimeError("Missing required secret(s): DOCUMENT_INTELLIGENCE_ENDPOINT")

        monkeypatch.setattr(document_intelligence_client, "analyze_document", broken)

        resp = api.verify_document_endpoint(_req(method="POST", route_params={"candidate_id": "abc"}, body=self._body()))
        assert resp.status_code == 503
        assert "DOCUMENT_INTELLIGENCE_ENDPOINT" in json.loads(resp.get_body())["error"]

    def test_adversarial_audit_log_failure_does_not_break_the_response(self, monkeypatch):
        """The verification result itself (the valuable output) must
        still come back even if writing the audit trail fails."""
        import document_intelligence_client
        monkeypatch.setattr(document_intelligence_client, "analyze_document", lambda file_bytes, **kw: {
            "Name": {"value": "Priya Sharma", "confidence": 0.98},
        })

        def broken_audit(entry):
            raise RuntimeError("Cosmos DB not configured")

        monkeypatch.setattr(repositories, "append_audit_log", broken_audit)

        resp = api.verify_document_endpoint(_req(
            method="POST", route_params={"candidate_id": "abc"},
            body=self._body(claimed_fields={"Name": "Priya Sharma"}),
        ))
        assert resp.status_code == 200
        assert json.loads(resp.get_body())["summary"].startswith("OK:")
