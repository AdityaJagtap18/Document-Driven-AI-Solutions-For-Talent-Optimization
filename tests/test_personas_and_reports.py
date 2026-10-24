"""Tests for the 8 persona-mapped roles added to auth.DEFAULT_ROLES (CDPI
Product Personas and Reporting Requirements doc) and the two new real
reports built from existing data: the Resume Processing Report (AI
Observability trace log) and the Communication Delivery Report
(comms_delivery_log)."""

import json

import api_blueprint as api
import auth
import observability
import persona_catalogue
import repositories
from conftest import _req


_RECRUITER_ROLE_DOC = {"id": "recruiter", "role_key": "recruiter", "display_name": "Recruiter", "is_system": True}
_RECRUITER_USER = {"id": "u-rec", "username": "rec", "role_keys": ["recruiter"], "status": "active"}


class TestPersonaRoles:
    _EXPECTED_ROLE_KEYS = {
        "admin", "recruiter", "interviewer", "candidate",
        "hiring_manager", "recruitment_ops_manager", "hr_director",
        "recruitment_administrator", "ai_configuration_administrator",
        "azure_cloud_administrator", "ai_operations_mlops", "security_compliance_auditor",
    }

    def test_golden_all_12_persona_roles_exist(self):
        actual = {r["role_key"] for r in auth.DEFAULT_ROLES}
        assert actual == self._EXPECTED_ROLE_KEYS

    def test_golden_every_permission_id_referenced_is_real(self):
        for role in auth.DEFAULT_ROLES:
            for pid in role["permission_ids"]:
                assert pid in auth.PERMISSION_IDS, f"{role['role_key']} references unknown permission '{pid}'"

    def test_golden_hr_director_is_aggregate_only_no_candidate_access(self):
        role = next(r for r in auth.DEFAULT_ROLES if r["role_key"] == "hr_director")
        assert role["permission_ids"] == ["analytics.read"]

    def test_golden_ai_configuration_administrator_has_no_azure_or_secret_access(self):
        role = next(r for r in auth.DEFAULT_ROLES if r["role_key"] == "ai_configuration_administrator")
        for pid in role["permission_ids"]:
            assert not pid.startswith("azure.")
            assert not pid.startswith("secrets.")

    def test_golden_azure_cloud_administrator_has_no_candidate_scoring_access(self):
        role = next(r for r in auth.DEFAULT_ROLES if r["role_key"] == "azure_cloud_administrator")
        for pid in role["permission_ids"]:
            assert not pid.startswith("rag.")
            assert not pid.startswith("candidates.")

    def test_golden_security_compliance_auditor_is_read_only(self):
        role = next(r for r in auth.DEFAULT_ROLES if r["role_key"] == "security_compliance_auditor")
        for pid in role["permission_ids"]:
            assert ".write" not in pid and ".activate" not in pid and ".manage" not in pid

    def test_golden_every_persona_role_is_marked_system(self):
        for role in auth.DEFAULT_ROLES:
            assert role["is_system"] is True

    def test_adversarial_no_duplicate_role_keys(self):
        keys = [r["role_key"] for r in auth.DEFAULT_ROLES]
        assert len(keys) == len(set(keys))


class TestResumeProcessingReportEndpoint:
    def test_golden_computes_real_outcome_counts(self, tmp_path, monkeypatch):
        trace_path = tmp_path / "ai_traces.jsonl"
        trace_path.write_text(
            json.dumps({"stage": "document_extraction", "status": "succeeded", "correlation_id": "c1", "logged_at": "2026-01-01T00:00:00Z"}) + "\n"
            + json.dumps({"stage": "document_extraction", "status": "failed", "correlation_id": "c2", "error": "PDF parse error", "logged_at": "2026-01-02T00:00:00Z"}) + "\n"
            + json.dumps({"stage": "chunking", "status": "succeeded", "correlation_id": "c1", "logged_at": "2026-01-01T00:00:01Z"}) + "\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(observability, "TRACE_LOG_PATH", trace_path)
        resp = api.resume_processing_report_endpoint(_req())
        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["total_processing_attempts"] == 2  # only document_extraction spans, chunking excluded
        assert body["succeeded"] == 1
        assert body["failed_count"] == 1
        assert body["failure_rate"] == 0.5
        assert body["recent_failures"][0]["correlation_id"] == "c2"
        assert body["pending_queue_depth"] is None  # no queue concept -- honestly null, never invented

    def test_golden_empty_log_returns_real_zeros_not_fake_data(self, tmp_path, monkeypatch):
        monkeypatch.setattr(observability, "TRACE_LOG_PATH", tmp_path / "ai_traces.jsonl")
        resp = api.resume_processing_report_endpoint(_req())
        body = json.loads(resp.get_body())
        assert body["total_processing_attempts"] == 0
        assert body["failure_rate"] is None  # no attempts -- not fabricated as 0%

    def test_adversarial_recruiter_cannot_view(self, monkeypatch):
        import azure.functions as func

        monkeypatch.setattr(repositories, "get_user", lambda uid: _RECRUITER_USER if uid == "u-rec" else None)
        monkeypatch.setattr(repositories, "get_role", lambda key: _RECRUITER_ROLE_DOC if key == "recruiter" else None)
        token = auth.create_token(_RECRUITER_USER)
        resp = api.resume_processing_report_endpoint(func.HttpRequest(method="GET", url="/api/test", body=None, headers={"Authorization": f"Bearer {token}"}))
        assert resp.status_code == 403


class TestCommunicationDeliveryReportEndpoint:
    def test_golden_aggregates_by_channel(self, monkeypatch):
        monkeypatch.setattr(repositories, "list_all_comms_deliveries", lambda limit=500: [
            {"channel": "whatsapp", "outcome": "provider_accepted", "correlation_id": "c1"},
            {"channel": "whatsapp", "outcome": "failed", "correlation_id": "c2"},
            {"channel": "whatsapp", "outcome": "provider_accepted", "correlation_id": "c3"},
        ])
        resp = api.communication_delivery_report_endpoint(_req())
        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["count"] == 3
        assert body["by_channel"]["whatsapp"] == {"submitted": 3, "provider_accepted": 2, "failed": 1}
        assert "email" not in body["by_channel"]  # honestly absent, not a fabricated zero row

    def test_golden_empty_log_returns_real_zeros(self, monkeypatch):
        monkeypatch.setattr(repositories, "list_all_comms_deliveries", lambda limit=500: [])
        resp = api.communication_delivery_report_endpoint(_req())
        body = json.loads(resp.get_body())
        assert body["count"] == 0
        assert body["by_channel"] == {}

    def test_adversarial_recruiter_cannot_view(self, monkeypatch):
        import azure.functions as func

        monkeypatch.setattr(repositories, "get_user", lambda uid: _RECRUITER_USER if uid == "u-rec" else None)
        monkeypatch.setattr(repositories, "get_role", lambda key: _RECRUITER_ROLE_DOC if key == "recruiter" else None)
        token = auth.create_token(_RECRUITER_USER)
        resp = api.communication_delivery_report_endpoint(func.HttpRequest(method="GET", url="/api/test", body=None, headers={"Authorization": f"Bearer {token}"}))
        assert resp.status_code == 403


class TestPersonaCatalogue:
    """Section 8 of the CDPI Product Personas doc: one representative
    persona per persona type, with role mapping, page/report visibility,
    and restriction all derived from the REAL auth.DEFAULT_ROLES/PAGES
    data rather than a separate hand-maintained authorization table."""

    def test_golden_catalogue_is_internally_consistent(self):
        problems = persona_catalogue.validate_persona_catalogue()
        assert problems == [], problems

    def test_golden_exactly_12_personas_matching_the_12_roles(self):
        assert len(persona_catalogue.PERSONAS) == 12
        persona_role_keys = {p["role_key"] for p in persona_catalogue.PERSONAS}
        role_keys = {r["role_key"] for r in auth.DEFAULT_ROLES}
        assert persona_role_keys == role_keys

    def test_golden_every_report_slug_used_by_a_persona_exists_in_catalogue(self):
        for persona in persona_catalogue.PERSONAS:
            for slug in persona["report_slugs"]:
                assert slug in persona_catalogue.REPORTS

    def test_golden_every_report_status_is_one_of_the_three_documented_states(self):
        for report in persona_catalogue.REPORTS.values():
            assert report["status"] in ("available", "planned", "data_dependent")

    def test_golden_hr_director_sees_executive_overview_not_candidate_dashboard(self):
        # Explicit example from the spec (Section 8.6): HR Director should
        # access the Executive Recruitment Overview while being denied
        # candidate-record access -- analytics.read only, no
        # candidates.read, so the candidate dashboard page is restricted.
        persona = persona_catalogue.PERSONAS_BY_ID["P06"]
        allowed = persona_catalogue.allowed_pages(persona)
        assert "admin_reports" in allowed
        assert "dashboard" not in allowed
        statuses = {r["slug"]: r for r in persona_catalogue.persona_report_table(persona)}
        assert statuses["executive_recruitment_overview"]["authorized"] is True

    def test_golden_interviewer_sees_only_assigned_interview_pages_not_recruitment_reports(self):
        # Explicit example from the spec (Section 8.6): Interviewer should
        # see interview information, not unrestricted recruitment reports.
        persona = persona_catalogue.PERSONAS_BY_ID["P04"]
        allowed = persona_catalogue.allowed_pages(persona)
        assert "pipeline" in allowed  # interviews.manage
        assert "admin_reports" not in allowed  # no analytics.read
        assert "recruiter_reports" not in allowed  # no screening.propose

    def test_golden_candidate_is_self_scoped_only(self):
        persona = persona_catalogue.PERSONAS_BY_ID["P01"]
        role = next(r for r in auth.DEFAULT_ROLES if r["role_key"] == "candidate")
        assert role["permission_ids"] == []
        assert persona_catalogue.allowed_pages(persona) == ["candidate_portal"]

    def test_golden_describe_persona_records_all_section_8_5_fields(self):
        for persona in persona_catalogue.PERSONAS:
            record = persona_catalogue.describe_persona(persona)
            for field in (
                "persona_id", "demo_display_name", "role_key", "responsibilities",
                "allowed_pages", "restricted_pages", "allowed_reports",
                "restricted_reports", "data_scope", "export_permission",
            ):
                assert field in record

    def test_adversarial_no_available_report_exceeds_its_personas_actual_permissions(self):
        # The spec's core guardrail (Section 8.3's point 4 and 8.6's
        # "absence of fabricated metrics"): a report can only be marked
        # authorized=True if the persona's role genuinely holds the gate.
        for persona in persona_catalogue.PERSONAS:
            role = next(r for r in auth.DEFAULT_ROLES if r["role_key"] == persona["role_key"])
            for entry in persona_catalogue.persona_report_table(persona):
                if entry["gating_permission"] is None:
                    continue
                expected = entry["gating_permission"] in role["permission_ids"]
                assert entry["authorized"] == expected

    def test_adversarial_only_admin_persona_has_export_permission(self):
        for persona in persona_catalogue.PERSONAS:
            if persona["persona_id"] == "P08":
                assert persona["export_permission"] is True
            else:
                assert persona["export_permission"] is False


class _FakeUsersContainer:
    """Minimal in-memory Cosmos stand-in supporting exactly the query
    shapes repositories.py's user functions issue (same convention as
    test_page_access.py's _FakeContainer, scoped to the 'users'
    container)."""

    def __init__(self):
        self.items = {}

    def upsert_item(self, doc):
        self.items[doc["id"]] = dict(doc)
        return dict(doc)

    def query_items(self, query, parameters=None, enable_cross_partition_query=None, partition_key=None):
        docs = list(self.items.values())
        if parameters:
            for param in parameters:
                if param["name"] == "@u":
                    docs = [d for d in docs if d.get("username") == param["value"]]
        return docs


class TestEnsureDemoPersonas:
    """repositories.ensure_demo_personas() -- opt-in only, never called at
    automatic bootstrap (see bootstrap_iam.py's --create-demo-personas
    flag, the only caller)."""

    def test_golden_creates_one_account_per_persona_with_random_password(self, monkeypatch):
        container = _FakeUsersContainer()
        monkeypatch.setattr(repositories, "get_container", lambda name: container)
        created = repositories.ensure_demo_personas()
        assert len(created) == 12
        usernames = {c["username"] for c in created}
        assert usernames == {f"demo_{p['role_key']}" for p in persona_catalogue.PERSONAS}
        # passwords are random and distinct, never a fixed/default value
        passwords = {c["password"] for c in created}
        assert len(passwords) == 12
        for doc in container.items.values():
            assert doc["is_demo_persona"] is True
            assert doc["password_hash"] != ""

    def test_golden_idempotent_does_not_recreate_or_reset_existing_demo_accounts(self, monkeypatch):
        container = _FakeUsersContainer()
        monkeypatch.setattr(repositories, "get_container", lambda name: container)
        first = repositories.ensure_demo_personas()
        assert len(first) == 12
        original_hash = container.items[next(iter(container.items))]["password_hash"]
        second = repositories.ensure_demo_personas()
        assert second == []
        # no existing demo account's password was touched by the second call
        assert all(doc["password_hash"] for doc in container.items.values())
        assert container.items[next(iter(container.items))]["password_hash"] == original_hash


class TestPersonaCatalogueEndpoint:
    def test_golden_admin_can_view_full_catalogue(self):
        resp = api.persona_catalogue_endpoint(_req())
        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert len(body["personas"]) == 12
        assert body["validation_problems"] == []

    def test_adversarial_recruiter_cannot_view(self, monkeypatch):
        import azure.functions as func

        monkeypatch.setattr(repositories, "get_user", lambda uid: _RECRUITER_USER if uid == "u-rec" else None)
        monkeypatch.setattr(repositories, "get_role", lambda key: _RECRUITER_ROLE_DOC if key == "recruiter" else None)
        token = auth.create_token(_RECRUITER_USER)
        resp = api.persona_catalogue_endpoint(func.HttpRequest(method="GET", url="/api/test", body=None, headers={"Authorization": f"Bearer {token}"}))
        assert resp.status_code == 403
