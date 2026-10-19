"""pytest config: makes semantic-library's, context-lake's, and
azure-functions' modules importable (same flat-import convention the
production code uses — the directory names have hyphens, so they can't be
dotted packages)."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "semantic-library"))
sys.path.insert(0, str(ROOT / "context-lake"))
sys.path.insert(0, str(ROOT / "azure-functions"))
sys.path.insert(0, str(ROOT / "voicebot"))

import azure.functions as func  # noqa: E402
import pytest  # noqa: E402

import auth  # noqa: E402
import repositories  # noqa: E402

# ---------------------------------------------------------------------------
# Shared IAM test fixtures -- a repo-wide autouse fixture (defined here, not
# in test_api_blueprint.py, specifically so it also applies to other test
# modules like test_page_access.py: pytest autouse fixtures only apply
# within the module/conftest scope they're DEFINED in, not wherever that
# module happens to be imported from -- an earlier version of this had it
# in test_api_blueprint.py and test_page_access.py silently got real 503s
# from hitting real Cosmos, since the autouse fixture never ran for it).
#
# Every endpoint test calls these route functions directly without ever
# constructing a real login. _req()'s default Authorization header carries
# a real JWT (signed with a fixed test secret, see _default_iam_fixtures
# below) for a fixture account that holds every permission in the catalog
# -- equivalent to "logged in as Admin", the top-level role in this app
# (no separate Super Admin tier) -- so tests keep exercising their own
# endpoint's actual logic unchanged. Tests that need to verify
# authorization itself (missing/invalid token, wrong permission,
# self-escalation, last-Admin protection) pass `headers=` explicitly.
# ---------------------------------------------------------------------------
_FIXTURE_ADMIN = {
    "id": "fixture-admin-id", "username": "fixture_admin",
    "password_hash": "", "display_name": "Fixture Admin", "email": "",
    "role_keys": ["admin"], "status": "active",
}
_FIXTURE_ADMIN_ROLE = {
    "id": "admin", "role_key": "admin", "is_system": True,
    "permission_ids": list(auth.PERMISSION_IDS),
}
_TEST_JWT_SECRET = "test-only-secret-never-used-outside-pytest"


@pytest.fixture(autouse=True)
def _default_iam_fixtures(monkeypatch):
    # Patches get_secret (the lower boundary), not _jwt_secret() itself --
    # so _jwt_secret()'s own real logic (raise RuntimeError when the
    # secret is missing) still runs under this fixture. test_auth.py's
    # test_adversarial_missing_jwt_secret_raises_clear_error further
    # monkeypatches get_secret to return None on top of this to exercise
    # exactly that path; patching _jwt_secret directly here would have
    # silently defeated that test instead.
    monkeypatch.setattr(auth, "get_secret", lambda name: _TEST_JWT_SECRET if name == "JWT_SECRET" else None)
    monkeypatch.setattr(
        repositories, "get_user",
        lambda uid: _FIXTURE_ADMIN if uid == _FIXTURE_ADMIN["id"] else None,
    )
    monkeypatch.setattr(
        repositories, "get_role",
        lambda key: _FIXTURE_ADMIN_ROLE if key == "admin" else None,
    )


def _fixture_token():
    return auth.create_token(_FIXTURE_ADMIN)


def _req(method="GET", route_params=None, body=None, headers=None):
    if headers is None:
        headers = {"Authorization": "Bearer " + _fixture_token()}
    return func.HttpRequest(
        method=method,
        url="/api/test",
        body=json.dumps(body).encode() if body is not None else None,
        route_params=route_params or {},
        headers=headers,
    )
