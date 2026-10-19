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
import auth

# _req()/_FIXTURE_ADMIN/_FIXTURE_ADMIN_ROLE/_fixture_token and the
# autouse _default_iam_fixtures fixture live in conftest.py, not here --
# that fixture needs to apply repo-wide (test_page_access.py uses the
# same fixture account), and pytest autouse fixtures only apply within
# the module/conftest scope they're actually DEFINED in. See conftest.py
# for the full rationale.
from conftest import _FIXTURE_ADMIN, _FIXTURE_ADMIN_ROLE, _fixture_token, _req  # noqa: E402,F401


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
    def test_golden_returns_jd_ids_filenames_and_titles(self, monkeypatch):
        monkeypatch.setattr(
            jd_manager, "list_jds",
            lambda jd_folder: [
                {"jd_id": "ml_developer", "jd_filename": "ml_developer.txt", "title": "Machine Learning Developer", "path": "x", "text": "y"},
            ],
        )
        resp = api.list_jds_endpoint(_req())
        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body == {
            "count": 1,
            "jds": [{"jd_id": "ml_developer", "jd_filename": "ml_developer.txt", "title": "Machine Learning Developer"}],
        }

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
                "candidate_id": "c1", "Name": "Jane Doe", "resume_name": "jane.pdf", "ranking_tier": "To be Considered",
                "ingested_at": "2026-01-01", "HR Notes": "confidential", "Red Flags & Risk Analysis": ["secret"],
            },
        )
        monkeypatch.setattr(repositories, "list_audit_log", lambda candidate_id: [])
        monkeypatch.setattr(repositories, "get_pipeline_config", lambda: ["Shortlisted", "Selected"])
        resp = api.get_candidate_by_email_endpoint(_req(route_params={"email": "jane@example.com"}))
        body = json.loads(resp.get_body())
        assert body == {
            "Name": "Jane Doe", "resume_name": "jane.pdf",
            "ranking_tier": "To be Considered", "ingested_at": "2026-01-01",
            "stage_timeline": [], "pipeline_stages": ["Shortlisted", "Selected"],
        }
        assert "HR Notes" not in body
        assert "Red Flags & Risk Analysis" not in body

    def test_golden_includes_stage_timeline(self, monkeypatch):
        monkeypatch.setattr(
            repositories, "find_candidate_by_email",
            lambda email: {"candidate_id": "c1", "Name": "Jane Doe", "resume_name": "jane.pdf", "ranking_tier": "To be Considered", "ingested_at": "2026-01-01"},
        )
        monkeypatch.setattr(
            repositories, "list_audit_log",
            lambda candidate_id: [
                {"action": "application_submitted"},  # not a stage_update -- excluded
                {"action": "stage_update", "stage": "Shortlisted", "updated_by": "Priyanka", "notes": "", "logged_at": "2026-01-02"},
                {"action": "stage_update", "stage": "Aptitude Exam", "updated_by": "Priyanka", "notes": "", "logged_at": "2026-01-03"},
            ],
        )
        monkeypatch.setattr(repositories, "get_pipeline_config", lambda: ["Shortlisted", "Aptitude Exam", "Selected"])
        resp = api.get_candidate_by_email_endpoint(_req(route_params={"email": "jane@example.com"}))
        body = json.loads(resp.get_body())
        assert [t["stage"] for t in body["stage_timeline"]] == ["Shortlisted", "Aptitude Exam"]
        assert body["pipeline_stages"] == ["Shortlisted", "Aptitude Exam", "Selected"]

    def test_edge_pipeline_config_unavailable_falls_back_to_empty_list(self, monkeypatch):
        monkeypatch.setattr(
            repositories, "find_candidate_by_email",
            lambda email: {"candidate_id": "c1", "Name": "Jane Doe", "resume_name": "jane.pdf", "ranking_tier": "To be Considered", "ingested_at": "2026-01-01"},
        )
        monkeypatch.setattr(repositories, "list_audit_log", lambda candidate_id: [])

        def broken():
            raise RuntimeError("Missing required secret(s): COSMOS_ENDPOINT")

        monkeypatch.setattr(repositories, "get_pipeline_config", broken)
        resp = api.get_candidate_by_email_endpoint(_req(route_params={"email": "jane@example.com"}))
        # a pipeline-config hiccup must not break the rest of a real status response
        assert resp.status_code == 200
        assert json.loads(resp.get_body())["pipeline_stages"] == []

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
        req = func.HttpRequest(
            method="POST", url="/api/test", body=b"not json", route_params={"candidate_id": "abc"},
            headers={"Authorization": "Bearer " + _fixture_token()},
        )
        resp = api.review_candidate_endpoint(req)
        assert resp.status_code == 400


class TestProposeBulkDecision:
    def _body(self, **overrides):
        base = {"decision": "reject", "candidate_ids": ["c1", "c2"], "proposed_by": "Priyanka", "notes": "Low scores"}
        base.update(overrides)
        return base

    def test_golden_records_pending_proposal_without_touching_candidates(self, monkeypatch):
        created = {}
        monkeypatch.setattr(
            repositories, "create_bulk_decision",
            lambda decision, candidate_ids, proposed_by, notes="": created.update(
                decision=decision, candidate_ids=candidate_ids, proposed_by=proposed_by, notes=notes,
            ) or {"id": "bd1"},
        )
        touched = {"called": False}
        monkeypatch.setattr(repositories, "upsert_candidate", lambda c: touched.update(called=True))

        resp = api.propose_bulk_decision_endpoint(_req(method="POST", body=self._body()))
        assert resp.status_code == 202
        body = json.loads(resp.get_body())
        assert body["status"] == "pending"
        assert body["candidate_count"] == 2
        assert created["decision"] == "reject"
        assert created["candidate_ids"] == ["c1", "c2"]
        assert touched["called"] is False  # the whole point: nothing applied yet

    def test_edge_invalid_decision_rejected(self, monkeypatch):
        resp = api.propose_bulk_decision_endpoint(_req(method="POST", body=self._body(decision="maybe")))
        assert resp.status_code == 400

    def test_edge_empty_candidate_ids_rejected(self, monkeypatch):
        resp = api.propose_bulk_decision_endpoint(_req(method="POST", body=self._body(candidate_ids=[])))
        assert resp.status_code == 400

    def test_edge_missing_proposed_by_rejected(self, monkeypatch):
        resp = api.propose_bulk_decision_endpoint(_req(method="POST", body=self._body(proposed_by="")))
        assert resp.status_code == 400

    def test_adversarial_cosmos_unavailable_returns_503(self, monkeypatch):
        def broken(decision, candidate_ids, proposed_by, notes=""):
            raise RuntimeError("Missing required secret(s): COSMOS_ENDPOINT")

        monkeypatch.setattr(repositories, "create_bulk_decision", broken)
        resp = api.propose_bulk_decision_endpoint(_req(method="POST", body=self._body()))
        assert resp.status_code == 503


class TestListBulkDecisions:
    def test_golden_returns_decisions(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            repositories, "list_bulk_decisions",
            lambda status=None: captured.update(status=status) or [{"id": "bd1", "status": "pending"}],
        )
        req = func.HttpRequest(
            method="GET", url="/api/test", body=None, params={"status": "pending"},
            headers={"Authorization": "Bearer " + _fixture_token()},
        )
        resp = api.list_bulk_decisions_endpoint(req)
        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["count"] == 1
        assert captured["status"] == "pending"

    def test_adversarial_cosmos_unavailable_returns_503(self, monkeypatch):
        def broken(status=None):
            raise RuntimeError("Missing required secret(s): COSMOS_ENDPOINT")

        monkeypatch.setattr(repositories, "list_bulk_decisions", broken)
        resp = api.list_bulk_decisions_endpoint(_req())
        assert resp.status_code == 503


class TestApproveBulkDecision:
    def test_golden_applies_decision_to_every_candidate(self, monkeypatch):
        monkeypatch.setattr(
            repositories, "get_bulk_decision",
            lambda decision_id: {"id": decision_id, "status": "pending", "decision": "reject", "candidate_ids": ["c1", "c2"], "notes": "Low scores"},
        )
        monkeypatch.setattr(repositories, "get_candidate", lambda cid: {"candidate_id": cid, "Name": "Jane"})
        upserted = []
        monkeypatch.setattr(repositories, "upsert_candidate", lambda c: upserted.append(dict(c)))
        logged = []
        monkeypatch.setattr(repositories, "append_audit_log", lambda entry: logged.append(entry))
        marked = {}
        monkeypatch.setattr(
            repositories, "mark_bulk_decision_reviewed",
            lambda decision_id, status, reviewed_by, extra=None: marked.update(
                decision_id=decision_id, status=status, reviewed_by=reviewed_by, extra=extra,
            ),
        )

        resp = api.approve_bulk_decision_endpoint(_req(method="POST", route_params={"decision_id": "bd1"}, body={"reviewed_by": "Admin Amit"}))
        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["status"] == "approved"
        assert [c["candidate_id"] for c in upserted] == ["c1", "c2"]
        assert all(c["ranking_tier"] == "Rejected" for c in upserted)
        assert all(e["action"] == "human_review_reject" for e in logged)
        assert marked["status"] == "approved"
        assert marked["reviewed_by"] == "Admin Amit"

    def test_edge_already_reviewed_returns_409(self, monkeypatch):
        monkeypatch.setattr(
            repositories, "get_bulk_decision",
            lambda decision_id: {"id": decision_id, "status": "approved", "decision": "approve", "candidate_ids": ["c1"]},
        )
        resp = api.approve_bulk_decision_endpoint(_req(method="POST", route_params={"decision_id": "bd1"}, body={"reviewed_by": "Admin Amit"}))
        assert resp.status_code == 409

    def test_edge_not_found_returns_404(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_bulk_decision", lambda decision_id: None)
        resp = api.approve_bulk_decision_endpoint(_req(method="POST", route_params={"decision_id": "missing"}, body={"reviewed_by": "Admin Amit"}))
        assert resp.status_code == 404

    def test_edge_missing_reviewed_by_returns_400(self, monkeypatch):
        resp = api.approve_bulk_decision_endpoint(_req(method="POST", route_params={"decision_id": "bd1"}, body={}))
        assert resp.status_code == 400

    def test_adversarial_one_bad_candidate_id_does_not_abort_the_rest(self, monkeypatch):
        monkeypatch.setattr(
            repositories, "get_bulk_decision",
            lambda decision_id: {"id": decision_id, "status": "pending", "decision": "approve", "candidate_ids": ["missing", "c2"], "notes": ""},
        )
        monkeypatch.setattr(repositories, "get_candidate", lambda cid: None if cid == "missing" else {"candidate_id": cid})
        monkeypatch.setattr(repositories, "upsert_candidate", lambda c: c)
        monkeypatch.setattr(repositories, "append_audit_log", lambda entry: entry)
        monkeypatch.setattr(repositories, "mark_bulk_decision_reviewed", lambda *a, **kw: None)

        resp = api.approve_bulk_decision_endpoint(_req(method="POST", route_params={"decision_id": "bd1"}, body={"reviewed_by": "Admin Amit"}))
        assert resp.status_code == 200
        results = json.loads(resp.get_body())["results"]
        assert results[0]["status"] == "skipped: not found"
        assert results[1]["status"] == "applied"


class TestRejectBulkDecision:
    def test_golden_marks_rejected_without_touching_candidates(self, monkeypatch):
        touched = {"called": False}
        monkeypatch.setattr(repositories, "upsert_candidate", lambda c: touched.update(called=True))
        marked = {}
        monkeypatch.setattr(
            repositories, "mark_bulk_decision_reviewed",
            lambda decision_id, status, reviewed_by, extra=None: marked.update(
                decision_id=decision_id, status=status, reviewed_by=reviewed_by, extra=extra,
            ),
        )

        resp = api.reject_bulk_decision_endpoint(
            _req(method="POST", route_params={"decision_id": "bd1"}, body={"reviewed_by": "Admin Amit", "notes": "Too risky"})
        )
        assert resp.status_code == 200
        assert marked["status"] == "rejected"
        assert marked["extra"] == {"review_notes": "Too risky"}
        assert touched["called"] is False

    def test_edge_not_found_returns_404(self, monkeypatch):
        def broken(decision_id, status, reviewed_by, extra=None):
            raise ValueError(f"Bulk decision '{decision_id}' not found.")

        monkeypatch.setattr(repositories, "mark_bulk_decision_reviewed", broken)
        resp = api.reject_bulk_decision_endpoint(_req(method="POST", route_params={"decision_id": "missing"}, body={"reviewed_by": "Admin Amit"}))
        assert resp.status_code == 404

    def test_edge_already_reviewed_returns_409(self, monkeypatch):
        def broken(decision_id, status, reviewed_by, extra=None):
            raise ValueError("This proposal is already 'approved' — it can't be reviewed again.")

        monkeypatch.setattr(repositories, "mark_bulk_decision_reviewed", broken)
        resp = api.reject_bulk_decision_endpoint(_req(method="POST", route_params={"decision_id": "bd1"}, body={"reviewed_by": "Admin Amit"}))
        assert resp.status_code == 409

    def test_edge_missing_reviewed_by_returns_400(self, monkeypatch):
        resp = api.reject_bulk_decision_endpoint(_req(method="POST", route_params={"decision_id": "bd1"}, body={}))
        assert resp.status_code == 400


class TestPipelineConfig:
    def test_golden_get_returns_configured_stages(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_pipeline_config", lambda: ["Shortlisted", "Technical Exam", "Selected"])
        resp = api.get_pipeline_config_endpoint(_req())
        assert resp.status_code == 200
        assert json.loads(resp.get_body()) == {"stages": ["Shortlisted", "Technical Exam", "Selected"]}

    def test_adversarial_get_cosmos_unavailable_returns_503(self, monkeypatch):
        def broken():
            raise RuntimeError("Missing required secret(s): COSMOS_ENDPOINT")

        monkeypatch.setattr(repositories, "get_pipeline_config", broken)
        resp = api.get_pipeline_config_endpoint(_req())
        assert resp.status_code == 503

    def test_golden_put_replaces_stage_list(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(repositories, "set_pipeline_config", lambda stages: captured.update(stages=stages))
        resp = api.update_pipeline_config_endpoint(_req(method="POST", body={"stages": ["A", "B", "C"]}))
        assert resp.status_code == 200
        assert captured["stages"] == ["A", "B", "C"]

    def test_edge_empty_stage_list_rejected(self, monkeypatch):
        resp = api.update_pipeline_config_endpoint(_req(method="POST", body={"stages": []}))
        assert resp.status_code == 400

    def test_edge_non_string_stage_rejected(self, monkeypatch):
        resp = api.update_pipeline_config_endpoint(_req(method="POST", body={"stages": ["A", 5]}))
        assert resp.status_code == 400

    def test_edge_blank_stage_name_rejected(self, monkeypatch):
        resp = api.update_pipeline_config_endpoint(_req(method="POST", body={"stages": ["A", "   "]}))
        assert resp.status_code == 400

    def test_adversarial_missing_stages_key(self, monkeypatch):
        resp = api.update_pipeline_config_endpoint(_req(method="POST", body={}))
        assert resp.status_code == 400


class TestCandidateTimeline:
    def test_golden_returns_stage_updates_in_order(self, monkeypatch):
        monkeypatch.setattr(
            repositories, "list_audit_log",
            lambda candidate_id: [
                {"action": "stage_update", "stage": "Shortlisted", "updated_by": "Priyanka", "notes": "", "logged_at": "2026-01-01"},
                {"action": "human_review_approve"},  # not a stage event -- excluded
                {"action": "stage_update", "stage": "Technical Exam", "updated_by": "Priyanka", "notes": "went well", "logged_at": "2026-01-02"},
            ],
        )
        resp = api.get_candidate_timeline_endpoint(_req(route_params={"candidate_id": "c1"}))
        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert [t["stage"] for t in body["timeline"]] == ["Shortlisted", "Technical Exam"]

    def test_edge_no_stage_updates_yet_returns_empty_list(self, monkeypatch):
        monkeypatch.setattr(repositories, "list_audit_log", lambda candidate_id: [])
        resp = api.get_candidate_timeline_endpoint(_req(route_params={"candidate_id": "c1"}))
        assert json.loads(resp.get_body())["timeline"] == []

    def test_adversarial_cosmos_unavailable_returns_503(self, monkeypatch):
        def broken(candidate_id):
            raise RuntimeError("Missing required secret(s): COSMOS_ENDPOINT")

        monkeypatch.setattr(repositories, "list_audit_log", broken)
        resp = api.get_candidate_timeline_endpoint(_req(route_params={"candidate_id": "c1"}))
        assert resp.status_code == 503


class TestAdvanceCandidateStage:
    STAGES = ["Shortlisted", "Aptitude Exam", "Technical Exam", "Selected"]

    def _body(self, **overrides):
        base = {"stage": "Shortlisted", "updated_by": "Priyanka", "notes": "Scored 85%", "performance": "Good"}
        base.update(overrides)
        return base

    def _mock_backend(self, monkeypatch, past_stage_updates=None, candidate=None):
        monkeypatch.setattr(repositories, "get_candidate", lambda cid: candidate or {"candidate_id": cid, "Name": "Jane"})
        monkeypatch.setattr(repositories, "get_pipeline_config", lambda: self.STAGES)
        monkeypatch.setattr(
            repositories, "list_audit_log",
            lambda cid: [{"action": "stage_update", "stage": s} for s in (past_stage_updates or [])],
        )

    def test_golden_first_stage_from_not_started(self, monkeypatch):
        self._mock_backend(monkeypatch, past_stage_updates=[])
        logged = {}
        monkeypatch.setattr(repositories, "append_audit_log", lambda entry: logged.update(entry))

        resp = api.advance_candidate_stage_endpoint(
            _req(method="POST", route_params={"candidate_id": "c1"}, body=self._body(stage="Shortlisted"))
        )
        assert resp.status_code == 200
        assert logged["action"] == "stage_update"
        assert logged["stage"] == "Shortlisted"
        assert logged["updated_by"] == "Priyanka"
        assert logged["performance"] == "Good"

    def test_golden_advances_to_immediate_next_stage(self, monkeypatch):
        self._mock_backend(monkeypatch, past_stage_updates=["Shortlisted"])
        logged = {}
        monkeypatch.setattr(repositories, "append_audit_log", lambda entry: logged.update(entry))

        resp = api.advance_candidate_stage_endpoint(
            _req(method="POST", route_params={"candidate_id": "c1"}, body=self._body(stage="Aptitude Exam"))
        )
        assert resp.status_code == 200
        assert logged["stage"] == "Aptitude Exam"

    def test_adversarial_cannot_skip_ahead(self, monkeypatch):
        self._mock_backend(monkeypatch, past_stage_updates=["Shortlisted"])
        # Technical Exam is 2 stages ahead of Shortlisted -- Aptitude Exam is the only valid next stage
        resp = api.advance_candidate_stage_endpoint(
            _req(method="POST", route_params={"candidate_id": "c1"}, body=self._body(stage="Technical Exam"))
        )
        assert resp.status_code == 400

    def test_adversarial_cannot_skip_ahead_from_not_started(self, monkeypatch):
        self._mock_backend(monkeypatch, past_stage_updates=[])
        resp = api.advance_candidate_stage_endpoint(
            _req(method="POST", route_params={"candidate_id": "c1"}, body=self._body(stage="Aptitude Exam"))
        )
        assert resp.status_code == 400

    def test_edge_already_at_final_stage_rejected(self, monkeypatch):
        self._mock_backend(monkeypatch, past_stage_updates=["Shortlisted", "Aptitude Exam", "Technical Exam", "Selected"])
        resp = api.advance_candidate_stage_endpoint(
            _req(method="POST", route_params={"candidate_id": "c1"}, body=self._body(stage="Selected"))
        )
        assert resp.status_code == 400

    def test_edge_current_stage_removed_from_config_returns_409(self, monkeypatch):
        self._mock_backend(monkeypatch, past_stage_updates=["Phone Screen"])  # not in self.STAGES anymore
        resp = api.advance_candidate_stage_endpoint(
            _req(method="POST", route_params={"candidate_id": "c1"}, body=self._body(stage="Shortlisted"))
        )
        assert resp.status_code == 409

    def test_edge_candidate_not_found_returns_404(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_candidate", lambda cid: None)
        resp = api.advance_candidate_stage_endpoint(_req(method="POST", route_params={"candidate_id": "missing"}, body=self._body()))
        assert resp.status_code == 404

    def test_adversarial_stage_not_in_configured_list_rejected(self, monkeypatch):
        self._mock_backend(monkeypatch, past_stage_updates=[])
        resp = api.advance_candidate_stage_endpoint(
            _req(method="POST", route_params={"candidate_id": "c1"}, body=self._body(stage="Made Up Stage"))
        )
        assert resp.status_code == 400

    def test_edge_missing_stage_returns_400(self, monkeypatch):
        resp = api.advance_candidate_stage_endpoint(
            _req(method="POST", route_params={"candidate_id": "c1"}, body=self._body(stage=""))
        )
        assert resp.status_code == 400

    def test_edge_missing_updated_by_returns_400(self, monkeypatch):
        resp = api.advance_candidate_stage_endpoint(
            _req(method="POST", route_params={"candidate_id": "c1"}, body=self._body(updated_by=""))
        )
        assert resp.status_code == 400

    def test_edge_missing_notes_returns_400(self, monkeypatch):
        resp = api.advance_candidate_stage_endpoint(
            _req(method="POST", route_params={"candidate_id": "c1"}, body=self._body(notes=""))
        )
        assert resp.status_code == 400

    def test_edge_whitespace_only_notes_returns_400(self, monkeypatch):
        resp = api.advance_candidate_stage_endpoint(
            _req(method="POST", route_params={"candidate_id": "c1"}, body=self._body(notes="   "))
        )
        assert resp.status_code == 400

    def test_adversarial_invalid_performance_rating_rejected(self, monkeypatch):
        resp = api.advance_candidate_stage_endpoint(
            _req(method="POST", route_params={"candidate_id": "c1"}, body=self._body(performance="Amazing"))
        )
        assert resp.status_code == 400

    def test_edge_performance_is_optional(self, monkeypatch):
        self._mock_backend(monkeypatch, past_stage_updates=[])
        logged = {}
        monkeypatch.setattr(repositories, "append_audit_log", lambda entry: logged.update(entry))
        resp = api.advance_candidate_stage_endpoint(
            _req(method="POST", route_params={"candidate_id": "c1"}, body=self._body(stage="Shortlisted", performance=None))
        )
        assert resp.status_code == 200
        assert logged["performance"] is None

    def test_adversarial_cosmos_unavailable_returns_503(self, monkeypatch):
        def broken(cid):
            raise RuntimeError("Missing required secret(s): COSMOS_ENDPOINT")

        monkeypatch.setattr(repositories, "get_candidate", broken)
        resp = api.advance_candidate_stage_endpoint(_req(method="POST", route_params={"candidate_id": "c1"}, body=self._body()))
        assert resp.status_code == 503


class TestListAuditLog:
    def test_golden_returns_all_entries(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            repositories, "list_all_audit_log",
            lambda action=None: captured.update(action=action) or [
                {"candidate_id": "1", "action": "application_submitted"},
                {"candidate_id": "1", "action": "human_review_approve"},
            ],
        )
        resp = api.list_audit_log_endpoint(_req())
        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["count"] == 2
        assert captured["action"] is None

    def test_golden_passes_through_action_query_param(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            repositories, "list_all_audit_log",
            lambda action=None: captured.update(action=action) or [],
        )
        req = func.HttpRequest(
            method="GET", url="/api/test", body=None, params={"action": "human_review_approve"},
            headers={"Authorization": "Bearer " + _fixture_token()},
        )
        api.list_audit_log_endpoint(req)
        assert captured["action"] == "human_review_approve"

    def test_edge_no_entries_returns_empty_list(self, monkeypatch):
        monkeypatch.setattr(repositories, "list_all_audit_log", lambda action=None: [])
        resp = api.list_audit_log_endpoint(_req())
        assert json.loads(resp.get_body()) == {"count": 0, "entries": []}

    def test_adversarial_cosmos_unavailable_returns_503(self, monkeypatch):
        def broken(action=None):
            raise RuntimeError("Missing required secret(s): COSMOS_ENDPOINT")

        monkeypatch.setattr(repositories, "list_all_audit_log", broken)
        resp = api.list_audit_log_endpoint(_req())
        assert resp.status_code == 503


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


# ---------------------------------------------------------------------------
# IAM/RBAC: login, session resolution, and backend permission enforcement.
# ---------------------------------------------------------------------------
_RECRUITER_USER = {
    "id": "user-recruiter-1", "username": "pat_recruiter", "password_hash": auth.hash_password("correct-password-1"),
    "display_name": "Pat Recruiter", "email": "pat@example.com", "role_keys": ["recruiter"], "status": "active",
}
_DISABLED_USER = {
    "id": "user-disabled-1", "username": "disabled_dana", "password_hash": auth.hash_password("whatever-1"),
    "display_name": "Disabled Dana", "email": "", "role_keys": ["recruiter"], "status": "disabled",
}
_RECRUITER_ROLE = {
    "id": "recruiter", "role_key": "recruiter", "is_system": True,
    "permission_ids": next(r for r in auth.DEFAULT_ROLES if r["role_key"] == "recruiter")["permission_ids"],
}


def _recruiter_token():
    return auth.create_token(_RECRUITER_USER)


class TestLogin:
    def test_golden_correct_credentials_returns_token_and_safe_user(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_user_by_username", lambda u: _RECRUITER_USER if u == "pat_recruiter" else None)
        monkeypatch.setattr(repositories, "append_audit_log", lambda entry: None)
        resp = api.login_endpoint(_req(method="POST", body={"username": "pat_recruiter", "password": "correct-password-1"}))
        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert "token" in body
        assert body["user"]["username"] == "pat_recruiter"
        assert "password_hash" not in body["user"]
        assert "password" not in json.dumps(body)

    def test_adversarial_wrong_password_returns_401_generic_message(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_user_by_username", lambda u: _RECRUITER_USER if u == "pat_recruiter" else None)
        monkeypatch.setattr(repositories, "append_audit_log", lambda entry: None)
        resp = api.login_endpoint(_req(method="POST", body={"username": "pat_recruiter", "password": "wrong"}))
        assert resp.status_code == 401
        assert "Invalid username or password" in json.loads(resp.get_body())["error"]

    def test_adversarial_unknown_username_returns_same_401_message(self, monkeypatch):
        """Same error text as a wrong password -- never leak whether a
        username exists (username-enumeration protection)."""
        monkeypatch.setattr(repositories, "get_user_by_username", lambda u: None)
        resp = api.login_endpoint(_req(method="POST", body={"username": "ghost", "password": "anything"}))
        assert resp.status_code == 401
        assert "Invalid username or password" in json.loads(resp.get_body())["error"]

    def test_adversarial_disabled_account_cannot_log_in(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_user_by_username", lambda u: _DISABLED_USER if u == "disabled_dana" else None)
        resp = api.login_endpoint(_req(method="POST", body={"username": "disabled_dana", "password": "whatever-1"}))
        assert resp.status_code == 403

    def test_edge_missing_fields_returns_400(self):
        resp = api.login_endpoint(_req(method="POST", body={"username": "pat_recruiter"}))
        assert resp.status_code == 400


class TestRequirePermission:
    """Negative-path coverage of the core authorization gate itself --
    the mechanism every protected endpoint in this file relies on."""

    def test_adversarial_missing_authorization_header_returns_401(self):
        req = func.HttpRequest(method="GET", url="/api/test", body=None)
        resp = api.list_candidates_endpoint(req)
        assert resp.status_code == 401

    def test_adversarial_malformed_authorization_header_returns_401(self):
        req = func.HttpRequest(method="GET", url="/api/test", body=None, headers={"Authorization": "NotBearer xyz"})
        resp = api.list_candidates_endpoint(req)
        assert resp.status_code == 401

    def test_adversarial_garbage_token_returns_401(self):
        req = func.HttpRequest(method="GET", url="/api/test", body=None, headers={"Authorization": "Bearer not.a.real.jwt"})
        resp = api.list_candidates_endpoint(req)
        assert resp.status_code == 401

    def test_adversarial_disabled_users_token_is_rejected(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_user", lambda uid: _DISABLED_USER if uid == _DISABLED_USER["id"] else None)
        token = auth.create_token(_DISABLED_USER)
        req = func.HttpRequest(method="GET", url="/api/test", body=None, headers={"Authorization": f"Bearer {token}"})
        resp = api.list_candidates_endpoint(req)
        assert resp.status_code == 401

    def test_adversarial_user_lacking_permission_gets_403_not_candidate_data(self, monkeypatch):
        """A real recruiter account (candidates.read, not audit.read)
        hitting the audit log must be refused -- this is the concrete
        'frontend can't fake a role' guarantee: no header or body field
        the client could send would make this succeed."""
        monkeypatch.setattr(repositories, "get_user", lambda uid: _RECRUITER_USER if uid == _RECRUITER_USER["id"] else None)
        monkeypatch.setattr(repositories, "get_role", lambda key: _RECRUITER_ROLE if key == "recruiter" else None)
        req = func.HttpRequest(
            method="GET", url="/api/test", body=None, headers={"Authorization": f"Bearer {_recruiter_token()}"},
        )
        resp = api.list_audit_log_endpoint(req)
        assert resp.status_code == 403

    def test_golden_user_with_the_right_permission_succeeds(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_user", lambda uid: _RECRUITER_USER if uid == _RECRUITER_USER["id"] else None)
        monkeypatch.setattr(repositories, "get_role", lambda key: _RECRUITER_ROLE if key == "recruiter" else None)
        monkeypatch.setattr(repositories, "list_candidates", lambda ranking_tier=None: [])
        req = func.HttpRequest(
            method="GET", url="/api/test", body=None, headers={"Authorization": f"Bearer {_recruiter_token()}"},
        )
        resp = api.list_candidates_endpoint(req)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Admin Panel: /admin/users*, /admin/roles*, /admin/permissions.
# ---------------------------------------------------------------------------
class TestAdminPermissionsAndRoles:
    def test_golden_list_permissions_returns_full_catalog(self):
        resp = api.list_permissions_endpoint(_req())
        body = json.loads(resp.get_body())
        assert set(body["permissions"].keys()) == set(auth.PERMISSION_IDS)

    def test_golden_list_roles(self, monkeypatch):
        monkeypatch.setattr(repositories, "list_roles", lambda: [_RECRUITER_ROLE])
        resp = api.list_roles_endpoint(_req())
        assert resp.status_code == 200
        assert json.loads(resp.get_body())["count"] == 1

    def test_golden_create_custom_role(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_role", lambda key: _FIXTURE_ADMIN_ROLE if key == "admin" else None)
        captured = {}
        monkeypatch.setattr(
            repositories, "create_role",
            lambda role_key, display_name, description, permission_ids, is_system=False: captured.update(
                role_key=role_key, permission_ids=permission_ids,
            ) or {"id": role_key, "role_key": role_key, "display_name": display_name, "permission_ids": permission_ids},
        )
        resp = api.create_role_endpoint(_req(method="POST", body={
            "role_key": "hiring_manager", "display_name": "Hiring Manager",
            "description": "Views pipeline analytics.", "permission_ids": ["candidates.read", "analytics.read"],
        }))
        assert resp.status_code == 201
        assert captured["role_key"] == "hiring_manager"

    def test_adversarial_create_role_rejects_unknown_permission(self):
        resp = api.create_role_endpoint(_req(method="POST", body={
            "role_key": "x", "display_name": "X", "permission_ids": ["not.a.real.permission"],
        }))
        assert resp.status_code == 400

    def test_adversarial_cannot_edit_a_system_role(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_role", lambda key: _RECRUITER_ROLE if key == "recruiter" else (_FIXTURE_ADMIN_ROLE if key == "admin" else None))
        resp = api.update_role_endpoint(_req(
            method="PUT", route_params={"role_key": "recruiter"},
            body={"permission_ids": list(auth.PERMISSION_IDS)},
        ))
        assert resp.status_code == 403

    def test_golden_edit_a_custom_role(self, monkeypatch):
        custom_role = {"id": "hiring_manager", "role_key": "hiring_manager", "is_system": False, "permission_ids": ["candidates.read"]}
        monkeypatch.setattr(repositories, "get_role", lambda key: custom_role if key == "hiring_manager" else (_FIXTURE_ADMIN_ROLE if key == "admin" else None))
        monkeypatch.setattr(repositories, "update_role", lambda doc: doc)
        resp = api.update_role_endpoint(_req(
            method="PUT", route_params={"role_key": "hiring_manager"},
            body={"permission_ids": ["candidates.read", "analytics.read"]},
        ))
        assert resp.status_code == 200
        assert json.loads(resp.get_body())["permission_ids"] == ["candidates.read", "analytics.read"]


class TestAdminUsers:
    def _actor(self):
        return dict(_FIXTURE_ADMIN)

    def test_golden_list_users_never_includes_password_hash(self, monkeypatch):
        monkeypatch.setattr(repositories, "list_users", lambda: [_RECRUITER_USER])
        resp = api.list_users_endpoint(_req())
        body = json.loads(resp.get_body())
        assert "password_hash" not in json.dumps(body)
        assert body["users"][0]["username"] == "pat_recruiter"

    def test_golden_create_user_hashes_password_and_never_returns_it(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_user_by_username", lambda u: None)
        monkeypatch.setattr(repositories, "get_role", lambda key: _RECRUITER_ROLE if key == "recruiter" else (_FIXTURE_ADMIN_ROLE if key == "admin" else None))
        captured = {}

        def fake_create_user(username, password_hash, display_name, email, role_keys, created_by):
            captured["password_hash"] = password_hash
            return {"id": "new-user-1", "username": username, "display_name": display_name,
                    "email": email, "role_keys": role_keys, "status": "active"}

        monkeypatch.setattr(repositories, "create_user", fake_create_user)
        monkeypatch.setattr(repositories, "append_audit_log", lambda entry: None)

        resp = api.create_user_endpoint(_req(method="POST", body={
            "username": "new_recruiter", "password": "a-real-password-123",
            "display_name": "New Recruiter", "email": "nr@example.com", "role_keys": ["recruiter"],
        }))
        assert resp.status_code == 201
        body = json.loads(resp.get_body())
        assert "password" not in json.dumps(body)
        assert "password_hash" not in json.dumps(body)
        assert captured["password_hash"] != "a-real-password-123"
        assert auth.verify_password("a-real-password-123", captured["password_hash"])

    def test_adversarial_create_user_rejects_short_password(self):
        resp = api.create_user_endpoint(_req(method="POST", body={
            "username": "x", "password": "short", "display_name": "X", "role_keys": [],
        }))
        assert resp.status_code == 400

    def test_adversarial_duplicate_username_returns_409(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_user_by_username", lambda u: _RECRUITER_USER)
        resp = api.create_user_endpoint(_req(method="POST", body={
            "username": "pat_recruiter", "password": "a-real-password-123", "display_name": "Dup", "role_keys": [],
        }))
        assert resp.status_code == 409

    def test_adversarial_non_admin_cannot_create_an_admin(self, monkeypatch):
        """Admin is the top-level role in this app (no separate Super
        Admin tier). A lesser custom role that happens to combine
        users.create with roles.assign (but doesn't hold admin itself)
        must not be able to mint a brand-new peer Admin just by creating
        a fresh account with that role."""
        ops_user = {"id": "ops-1", "username": "ops1", "role_keys": ["ops_manager"], "status": "active"}
        ops_role = {"id": "ops_manager", "role_key": "ops_manager", "permission_ids": ["users.create", "roles.assign"]}
        monkeypatch.setattr(repositories, "get_user", lambda uid: ops_user if uid == "ops-1" else None)
        monkeypatch.setattr(repositories, "get_role", lambda key: ops_role if key == "ops_manager" else None)
        token = auth.create_token(ops_user)
        resp = api.create_user_endpoint(func.HttpRequest(
            method="POST", url="/api/test", body=json.dumps({
                "username": "sneaky", "password": "a-real-password-123", "display_name": "Sneaky",
                "role_keys": ["admin"],
            }).encode(),
            headers={"Authorization": f"Bearer {token}"},
        ))
        assert resp.status_code == 403

    def test_golden_update_user_profile(self, monkeypatch):
        user_doc = dict(_RECRUITER_USER)
        monkeypatch.setattr(repositories, "get_user", lambda uid: user_doc if uid == "user-recruiter-1" else (_FIXTURE_ADMIN if uid == _FIXTURE_ADMIN["id"] else None))
        monkeypatch.setattr(repositories, "update_user", lambda doc: doc)
        monkeypatch.setattr(repositories, "append_audit_log", lambda entry: None)
        resp = api.update_user_endpoint(_req(
            method="PUT", route_params={"user_id": "user-recruiter-1"}, body={"display_name": "Patricia Recruiter"},
        ))
        assert resp.status_code == 200
        assert json.loads(resp.get_body())["display_name"] == "Patricia Recruiter"

    def test_golden_disable_user(self, monkeypatch):
        user_doc = dict(_RECRUITER_USER)
        monkeypatch.setattr(repositories, "get_user", lambda uid: user_doc if uid == "user-recruiter-1" else (_FIXTURE_ADMIN if uid == _FIXTURE_ADMIN["id"] else None))
        monkeypatch.setattr(repositories, "update_user", lambda doc: doc)
        monkeypatch.setattr(repositories, "append_audit_log", lambda entry: None)
        resp = api.set_user_status_endpoint(_req(
            method="PUT", route_params={"user_id": "user-recruiter-1"}, body={"status": "disabled"},
        ))
        assert resp.status_code == 200
        assert json.loads(resp.get_body())["status"] == "disabled"

    def test_adversarial_cannot_disable_own_account(self):
        resp = api.set_user_status_endpoint(_req(
            method="PUT", route_params={"user_id": _FIXTURE_ADMIN["id"]}, body={"status": "disabled"},
        ))
        assert resp.status_code == 403

    def test_adversarial_cannot_disable_the_last_active_admin(self, monkeypatch):
        other_admin = dict(_FIXTURE_ADMIN, id="other-admin-id", username="other_super")
        monkeypatch.setattr(repositories, "get_user", lambda uid: other_admin if uid == "other-admin-id" else (_FIXTURE_ADMIN if uid == _FIXTURE_ADMIN["id"] else None))
        monkeypatch.setattr(repositories, "count_active_users_with_role", lambda role_key, exclude_user_id=None: 0)
        resp = api.set_user_status_endpoint(_req(
            method="PUT", route_params={"user_id": "other-admin-id"}, body={"status": "disabled"},
        ))
        assert resp.status_code == 409

    def test_golden_can_disable_an_admin_when_another_remains_active(self, monkeypatch):
        other_admin = dict(_FIXTURE_ADMIN, id="other-admin-id", username="other_super")
        monkeypatch.setattr(repositories, "get_user", lambda uid: other_admin if uid == "other-admin-id" else (_FIXTURE_ADMIN if uid == _FIXTURE_ADMIN["id"] else None))
        monkeypatch.setattr(repositories, "count_active_users_with_role", lambda role_key, exclude_user_id=None: 1)
        monkeypatch.setattr(repositories, "update_user", lambda doc: doc)
        monkeypatch.setattr(repositories, "append_audit_log", lambda entry: None)
        resp = api.set_user_status_endpoint(_req(
            method="PUT", route_params={"user_id": "other-admin-id"}, body={"status": "disabled"},
        ))
        assert resp.status_code == 200


class TestSetUserRoles:
    def test_golden_assigns_new_roles(self, monkeypatch):
        user_doc = dict(_RECRUITER_USER)
        monkeypatch.setattr(repositories, "get_user", lambda uid: user_doc if uid == "user-recruiter-1" else (_FIXTURE_ADMIN if uid == _FIXTURE_ADMIN["id"] else None))
        monkeypatch.setattr(repositories, "get_role", lambda key: ({"id": key, "role_key": key} if key in ("recruiter", "interviewer") else (_FIXTURE_ADMIN_ROLE if key == "admin" else None)))
        monkeypatch.setattr(repositories, "update_user", lambda doc: doc)
        monkeypatch.setattr(repositories, "append_audit_log", lambda entry: None)
        resp = api.set_user_roles_endpoint(_req(
            method="PUT", route_params={"user_id": "user-recruiter-1"}, body={"role_keys": ["recruiter", "interviewer"]},
        ))
        assert resp.status_code == 200
        assert json.loads(resp.get_body())["role_keys"] == ["recruiter", "interviewer"]

    def test_adversarial_cannot_assign_unknown_role(self, monkeypatch):
        user_doc = dict(_RECRUITER_USER)
        monkeypatch.setattr(repositories, "get_user", lambda uid: user_doc if uid == "user-recruiter-1" else (_FIXTURE_ADMIN if uid == _FIXTURE_ADMIN["id"] else None))
        monkeypatch.setattr(repositories, "get_role", lambda key: _FIXTURE_ADMIN_ROLE if key == "admin" else None)
        resp = api.set_user_roles_endpoint(_req(
            method="PUT", route_params={"user_id": "user-recruiter-1"}, body={"role_keys": ["made_up_role"]},
        ))
        assert resp.status_code == 400

    def test_adversarial_no_self_escalation(self):
        """A user can never change their own role assignments through
        this endpoint, even one who already holds roles.assign -- the
        explicit spec requirement this test exists to pin down."""
        resp = api.set_user_roles_endpoint(_req(
            method="PUT", route_params={"user_id": _FIXTURE_ADMIN["id"]}, body={"role_keys": ["recruiter"]},
        ))
        assert resp.status_code == 403

    def test_adversarial_non_admin_cannot_grant_admin_role(self, monkeypatch):
        """Same escalation path as test_adversarial_non_admin_cannot_create_an_admin
        above, but via role re-assignment instead of account creation."""
        ops_user = {"id": "ops-1", "username": "ops1", "role_keys": ["ops_manager"], "status": "active"}
        ops_role = {"id": "ops_manager", "role_key": "ops_manager", "permission_ids": ["roles.assign"]}
        target_user = dict(_RECRUITER_USER)

        def get_user(uid):
            return ops_user if uid == "ops-1" else (target_user if uid == "user-recruiter-1" else None)

        def get_role(key):
            if key == "ops_manager":
                return ops_role
            if key == "recruiter":
                return _RECRUITER_ROLE
            if key == "admin":
                return {"id": "admin", "role_key": "admin"}
            return None

        monkeypatch.setattr(repositories, "get_user", get_user)
        monkeypatch.setattr(repositories, "get_role", get_role)
        token = auth.create_token(ops_user)
        resp = api.set_user_roles_endpoint(func.HttpRequest(
            method="PUT", url="/api/test", route_params={"user_id": "user-recruiter-1"},
            body=json.dumps({"role_keys": ["admin"]}).encode(),
            headers={"Authorization": f"Bearer {token}"},
        ))
        assert resp.status_code == 403

    def test_adversarial_cannot_remove_admin_from_the_last_holder(self, monkeypatch):
        last_admin = dict(_FIXTURE_ADMIN, id="last-admin-id", username="last_super")
        monkeypatch.setattr(repositories, "get_user", lambda uid: last_admin if uid == "last-admin-id" else (_FIXTURE_ADMIN if uid == _FIXTURE_ADMIN["id"] else None))
        monkeypatch.setattr(repositories, "get_role", lambda key: _FIXTURE_ADMIN_ROLE if key == "admin" else ({"id": "recruiter", "role_key": "recruiter"} if key == "recruiter" else None))
        monkeypatch.setattr(repositories, "count_active_users_with_role", lambda role_key, exclude_user_id=None: 0)
        resp = api.set_user_roles_endpoint(_req(
            method="PUT", route_params={"user_id": "last-admin-id"}, body={"role_keys": ["recruiter"]},
        ))
        assert resp.status_code == 409
