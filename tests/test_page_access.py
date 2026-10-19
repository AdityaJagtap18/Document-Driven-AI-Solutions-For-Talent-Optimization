"""Tests for Dynamic Page Access Management -- repositories.sync_pages()
and the repositories.*page* CRUD (tested against a tiny fake Cosmos
container, the real boundary to the outside world -- no other test in
this suite exercises repositories.py's Cosmos calls directly, everything
else monkeypatches repositories.* wholesale; sync_pages()'s own
reconciliation logic lives IN repositories.py and calls other
repositories.* functions internally, so it needs this lower-level fake
to be tested for real rather than mocked away), plus the api_blueprint.py
admin/pages* endpoints (monkeypatched the usual way, same as every other
endpoint test in this suite)."""

import json

import azure.functions as func
import pytest
from azure.cosmos.exceptions import CosmosResourceNotFoundError

import api_blueprint as api
import auth
import client as context_lake_client
import repositories
from conftest import _req, _FIXTURE_ADMIN, _FIXTURE_ADMIN_ROLE, _fixture_token


class _FakeContainer:
    """Minimal in-memory stand-in for a Cosmos container client -- only
    supports the exact query shapes repositories.py's page-related
    functions actually issue (SELECT * FROM c, optionally filtered by
    c.page_id = @pid), not a general SQL interpreter."""

    def __init__(self):
        self.items = {}

    def upsert_item(self, doc):
        self.items[doc["id"]] = dict(doc)
        return dict(doc)

    def read_item(self, item, partition_key):
        if item not in self.items:
            raise CosmosResourceNotFoundError(message="not found")
        return dict(self.items[item])

    def query_items(self, query, parameters=None, enable_cross_partition_query=None, partition_key=None):
        docs = list(self.items.values())
        if parameters:
            for param in parameters:
                if param["name"] == "@pid":
                    docs = [d for d in docs if d.get("page_id") == param["value"]]
        if "ORDER BY c.module" in query:
            docs = sorted(docs, key=lambda d: (d.get("module", ""), d.get("name", "")))
        return docs


@pytest.fixture
def fake_containers(monkeypatch):
    """Patches context-lake/client.py's get_container() to hand back the
    same persistent _FakeContainer for 'pages' and 'page_access_policies'
    across every call within one test, exactly like a real Cosmos
    container does (not a fresh empty one each call)."""
    containers = {"pages": _FakeContainer(), "page_access_policies": _FakeContainer()}

    def fake_get_container(name):
        return containers[name]

    monkeypatch.setattr(context_lake_client, "get_container", fake_get_container)
    monkeypatch.setattr(repositories, "get_container", fake_get_container)
    return containers


# ---------------------------------------------------------------------------
# Discovery / sync (repository-level, against the fake Cosmos containers)
# ---------------------------------------------------------------------------
class TestSyncPages:
    def test_golden_first_run_registers_every_catalog_page_and_seeds_policies(self, fake_containers):
        result = repositories.sync_pages()
        assert result["first_run"] is True
        assert set(result["new"]) == set(auth.PAGE_IDS)
        assert result["updated"] == []
        assert result["stale"] == []

        persisted = {d["page_id"] for d in repositories.list_pages()}
        assert persisted == set(auth.PAGE_IDS)

        # Admin can reach every page out of the box (today's implicit
        # behavior, preserved as an explicit seeded policy) -- e.g. it
        # already holds candidates.read, so it should be allowed on the
        # dashboard page without anyone configuring it by hand.
        dashboard_policies = repositories.get_page_access_policies("dashboard")
        admin_policy = next(p for p in dashboard_policies if p["role_key"] == "admin")
        assert admin_policy["allowed"] is True

    def test_golden_candidate_only_granted_the_candidate_portal(self, fake_containers):
        repositories.sync_pages()
        for page_id in auth.PAGE_IDS:
            policies = repositories.get_page_access_policies(page_id)
            candidate_policy = next(p for p in policies if p["role_key"] == "candidate")
            expected = page_id == "candidate_portal"
            assert candidate_policy["allowed"] is expected, page_id

    def test_golden_rediscovery_is_idempotent_and_preserves_admin_edits(self, fake_containers):
        repositories.sync_pages()
        # Admin explicitly revokes Recruiter's access to the dashboard.
        repositories.set_page_access("dashboard", "recruiter", False, actor="alice")

        result = repositories.sync_pages()
        assert result["first_run"] is False
        assert result["new"] == []
        # The explicit revoke must survive being "rediscovered".
        policies = repositories.get_page_access_policies("dashboard")
        recruiter_policy = next(p for p in policies if p["role_key"] == "recruiter")
        assert recruiter_policy["allowed"] is False
        assert recruiter_policy["updated_by"] == "alice"

    def test_golden_new_catalog_page_is_discovered_with_no_policies(self, fake_containers, monkeypatch):
        repositories.sync_pages()  # first run, seeds the real catalog

        extra_page = {
            "page_id": "candidate_interview_scheduler", "name": "Interview Scheduler", "route": "scheduler",
            "module": "Interview Management", "route_type": "page", "is_protected": True,
            "required_permission": "interviews.manage", "description": "A hypothetical new page.",
        }
        monkeypatch.setattr(auth, "PAGES", auth.PAGES + [extra_page])
        monkeypatch.setattr(auth, "PAGE_IDS", auth.PAGE_IDS + ["candidate_interview_scheduler"])

        result = repositories.sync_pages()
        assert result["new"] == ["candidate_interview_scheduler"]

        page_doc = repositories.get_page("candidate_interview_scheduler")
        assert page_doc["discovery_status"] == "discovered"
        assert repositories.get_page_access_policies("candidate_interview_scheduler") == []

    def test_golden_removed_page_is_marked_stale_not_deleted(self, fake_containers, monkeypatch):
        repositories.sync_pages()
        monkeypatch.setattr(auth, "PAGE_IDS", [p for p in auth.PAGE_IDS if p != "pipeline"])

        result = repositories.sync_pages()
        assert result["stale"] == ["pipeline"]
        stale_doc = repositories.get_page("pipeline")
        assert stale_doc is not None
        assert stale_doc["discovery_status"] == "stale"
        # Its policies survive -- not deleted, in case the route comes back.
        assert repositories.get_page_access_policies("pipeline")

    def test_golden_a_stale_page_that_reappears_is_rediscovered_as_configured(self, fake_containers, monkeypatch):
        full_page_ids = list(auth.PAGE_IDS)
        repositories.sync_pages()
        monkeypatch.setattr(auth, "PAGE_IDS", [p for p in full_page_ids if p != "pipeline"])
        repositories.sync_pages()  # pipeline goes stale, but keeps its seeded policies
        assert repositories.get_page("pipeline")["discovery_status"] == "stale"

        monkeypatch.setattr(auth, "PAGE_IDS", full_page_ids)  # the route comes back
        repositories.sync_pages()
        assert repositories.get_page("pipeline")["discovery_status"] == "configured"

    def test_golden_metadata_change_is_reported_as_updated(self, fake_containers, monkeypatch):
        repositories.sync_pages()
        renamed = [dict(p, name="Hiring Pipeline") if p["page_id"] == "pipeline" else p for p in auth.PAGES]
        monkeypatch.setattr(auth, "PAGES", renamed)

        result = repositories.sync_pages()
        assert "pipeline" in result["updated"]
        assert repositories.get_page("pipeline")["name"] == "Hiring Pipeline"

    def test_edge_no_duplicate_page_ids_in_the_catalog(self):
        """Not a sync test -- a basic sanity check that the catalog itself
        can't accidentally define the same page_id twice."""
        assert len(auth.PAGE_IDS) == len(set(auth.PAGE_IDS))


class TestEffectivePages:
    def test_golden_deny_by_default_for_unconfigured_page(self):
        policies = [{"page_id": "dashboard", "role_key": "admin", "allowed": True}]
        assert auth.effective_pages(["recruiter"], policies) == set()

    def test_golden_grants_only_explicitly_allowed_pages(self):
        policies = [
            {"page_id": "dashboard", "role_key": "recruiter", "allowed": True},
            {"page_id": "users", "role_key": "recruiter", "allowed": False},
        ]
        assert auth.effective_pages(["recruiter"], policies) == {"dashboard"}

    def test_golden_unions_across_multiple_roles(self):
        policies = [
            {"page_id": "dashboard", "role_key": "recruiter", "allowed": True},
            {"page_id": "pipeline", "role_key": "interviewer", "allowed": True},
        ]
        assert auth.effective_pages(["recruiter", "interviewer"], policies) == {"dashboard", "pipeline"}


# ---------------------------------------------------------------------------
# Endpoint layer (api_blueprint.py) -- monkeypatched the usual way.
# ---------------------------------------------------------------------------
_RECRUITER_ROLE_DOC = {"id": "recruiter", "role_key": "recruiter", "display_name": "Recruiter", "is_system": True}
_ADMIN_ROLE_DOC = {"id": "admin", "role_key": "admin", "display_name": "Admin", "is_system": True}
_DASHBOARD_PAGE = {
    "id": "dashboard", "page_id": "dashboard", "name": "Dashboard", "route": "dashboard",
    "module": "Candidate Management", "required_permission": "candidates.read",
    "route_type": "page", "is_protected": True, "discovery_status": "configured",
}


class TestListPagesEndpoint:
    def test_golden_returns_pages_with_role_summary(self, monkeypatch):
        monkeypatch.setattr(repositories, "list_pages", lambda: [_DASHBOARD_PAGE])
        monkeypatch.setattr(
            repositories, "list_all_page_access_policies",
            lambda: [{"page_id": "dashboard", "role_key": "admin", "allowed": True},
                     {"page_id": "dashboard", "role_key": "recruiter", "allowed": False}],
        )
        resp = api.list_pages_endpoint(_req())
        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["pages"][0]["roles_granted"] == ["admin"]
        assert body["pages"][0]["roles_granted_count"] == 1

    def test_adversarial_recruiter_cannot_list_pages(self, monkeypatch):
        recruiter_user = {"id": "u-rec", "username": "rec", "role_keys": ["recruiter"], "status": "active"}
        monkeypatch.setattr(repositories, "get_user", lambda uid: recruiter_user if uid == "u-rec" else None)
        monkeypatch.setattr(repositories, "get_role", lambda key: _RECRUITER_ROLE_DOC if key == "recruiter" else None)
        token = auth.create_token(recruiter_user)
        resp = api.list_pages_endpoint(func.HttpRequest(
            method="GET", url="/api/test", body=None, headers={"Authorization": f"Bearer {token}"},
        ))
        assert resp.status_code == 403

    def test_adversarial_missing_token_returns_401(self):
        resp = api.list_pages_endpoint(func.HttpRequest(method="GET", url="/api/test", body=None))
        assert resp.status_code == 401


class TestSyncPagesEndpoint:
    def test_golden_runs_sync_and_audits_new_pages(self, monkeypatch):
        captured = []
        monkeypatch.setattr(
            repositories, "sync_pages",
            lambda: {"new": ["dashboard"], "updated": [], "stale": [], "first_run": True},
        )
        monkeypatch.setattr(repositories, "append_audit_log", lambda entry: captured.append(entry))
        resp = api.sync_pages_endpoint(_req(method="POST"))
        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["new"] == ["dashboard"]
        assert any(e["action"] == "page_discovered" and e["page_id"] == "dashboard" for e in captured)


class TestGetPageAccessEndpoint:
    def test_golden_reports_full_role_matrix_including_unconfigured_roles(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_page", lambda pid: _DASHBOARD_PAGE if pid == "dashboard" else None)
        monkeypatch.setattr(repositories, "list_roles", lambda: [_ADMIN_ROLE_DOC, _RECRUITER_ROLE_DOC])
        monkeypatch.setattr(
            repositories, "get_page_access_policies",
            lambda pid: [{"page_id": "dashboard", "role_key": "admin", "allowed": True}],
        )
        resp = api.get_page_access_endpoint(_req(route_params={"page_id": "dashboard"}))
        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        matrix = {r["role_key"]: r["allowed"] for r in body["access"]}
        assert matrix == {"admin": True, "recruiter": False}

    def test_adversarial_unknown_page_returns_404(self, monkeypatch):
        monkeypatch.setattr(repositories, "get_page", lambda pid: None)
        resp = api.get_page_access_endpoint(_req(route_params={"page_id": "does-not-exist"}))
        assert resp.status_code == 404


class TestSetPageAccessEndpoint:
    def test_golden_grants_and_revokes_in_one_batch(self, monkeypatch):
        captured = []
        monkeypatch.setattr(repositories, "get_page", lambda pid: dict(_DASHBOARD_PAGE))
        monkeypatch.setattr(repositories, "get_role", lambda key: _RECRUITER_ROLE_DOC if key == "recruiter" else (_FIXTURE_ADMIN_ROLE if key == "admin" else None))
        monkeypatch.setattr(repositories, "set_page_access", lambda pid, role, allowed, actor: captured.append((pid, role, allowed, actor)))
        monkeypatch.setattr(repositories, "update_page", lambda doc: doc)
        monkeypatch.setattr(repositories, "list_roles", lambda: [_ADMIN_ROLE_DOC, _RECRUITER_ROLE_DOC])
        monkeypatch.setattr(repositories, "get_page_access_policies", lambda pid: [])
        monkeypatch.setattr(repositories, "append_audit_log", lambda entry: None)

        resp = api.set_page_access_endpoint(_req(
            method="PUT", route_params={"page_id": "dashboard"},
            body={"policies": [{"role_key": "recruiter", "allowed": True}, {"role_key": "admin", "allowed": False}]},
        ))
        assert resp.status_code == 200
        assert ("dashboard", "recruiter", True, _FIXTURE_ADMIN["username"]) in captured
        assert ("dashboard", "admin", False, _FIXTURE_ADMIN["username"]) in captured

    def test_adversarial_unknown_role_rejected_before_any_write(self, monkeypatch):
        written = []
        monkeypatch.setattr(repositories, "get_page", lambda pid: dict(_DASHBOARD_PAGE))
        monkeypatch.setattr(repositories, "get_role", lambda key: _FIXTURE_ADMIN_ROLE if key == "admin" else None)
        monkeypatch.setattr(repositories, "set_page_access", lambda *a, **kw: written.append(a))

        resp = api.set_page_access_endpoint(_req(
            method="PUT", route_params={"page_id": "dashboard"},
            body={"policies": [{"role_key": "made_up_role", "allowed": True}]},
        ))
        assert resp.status_code == 400
        assert written == []

    def test_adversarial_non_bool_allowed_rejected(self):
        resp = api.set_page_access_endpoint(_req(
            method="PUT", route_params={"page_id": "dashboard"},
            body={"policies": [{"role_key": "recruiter", "allowed": "yes"}]},
        ))
        assert resp.status_code == 400

    def test_adversarial_recruiter_cannot_change_page_access(self, monkeypatch):
        recruiter_user = {"id": "u-rec", "username": "rec", "role_keys": ["recruiter"], "status": "active"}
        monkeypatch.setattr(repositories, "get_user", lambda uid: recruiter_user if uid == "u-rec" else None)
        monkeypatch.setattr(repositories, "get_role", lambda key: _RECRUITER_ROLE_DOC if key == "recruiter" else None)
        token = auth.create_token(recruiter_user)
        resp = api.set_page_access_endpoint(func.HttpRequest(
            method="PUT", url="/api/test", route_params={"page_id": "dashboard"},
            body=json.dumps({"policies": [{"role_key": "recruiter", "allowed": True}]}).encode(),
            headers={"Authorization": f"Bearer {token}"},
        ))
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# /auth/me now embeds the caller's effective pages.
# ---------------------------------------------------------------------------
class TestMeEndpointPages:
    def test_golden_me_includes_effective_pages(self, monkeypatch):
        monkeypatch.setattr(
            repositories, "list_all_page_access_policies",
            lambda: [{"page_id": "dashboard", "role_key": "admin", "allowed": True}],
        )
        resp = api.me_endpoint(_req())
        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["pages"] == ["dashboard"]
