"""Tests for auth.py — password hashing, JWT issuance/verification, and
the permission catalog's internal consistency."""

import datetime

import jwt as pyjwt
import pytest

import auth


class TestPasswordHashing:
    def test_golden_hash_then_verify_round_trips(self):
        h = auth.hash_password("correct horse battery staple")
        assert auth.verify_password("correct horse battery staple", h) is True

    def test_golden_wrong_password_fails(self):
        h = auth.hash_password("correct horse battery staple")
        assert auth.verify_password("wrong password", h) is False

    def test_golden_hash_is_never_the_plaintext(self):
        h = auth.hash_password("correct horse battery staple")
        assert h != "correct horse battery staple"
        assert "correct horse battery staple" not in h

    def test_edge_same_password_hashes_differently_each_time(self):
        """bcrypt salts automatically -- two hashes of the same password
        must differ, otherwise two users with the same password would be
        trivially detectable by comparing hashes."""
        h1 = auth.hash_password("correct horse battery staple")
        h2 = auth.hash_password("correct horse battery staple")
        assert h1 != h2

    def test_adversarial_corrupt_hash_fails_closed(self):
        assert auth.verify_password("anything", "not-a-real-bcrypt-hash") is False

    def test_adversarial_empty_hash_fails_closed(self):
        assert auth.verify_password("anything", "") is False


class TestTokens:
    def test_golden_create_then_verify_round_trips(self, monkeypatch):
        monkeypatch.setattr(auth, "_jwt_secret", lambda: "test-secret")
        token = auth.create_token({"id": "u1", "username": "alice"})
        payload = auth.verify_token(token)
        assert payload["sub"] == "u1"
        assert payload["username"] == "alice"

    def test_golden_token_does_not_embed_roles(self, monkeypatch):
        """Roles/permissions must always be re-read from the database at
        request time, never trusted from a token's own claims -- see
        create_token()'s docstring. A token for a user with no 'role_keys'
        key at all should still work and simply carry no role claim."""
        monkeypatch.setattr(auth, "_jwt_secret", lambda: "test-secret")
        token = auth.create_token({"id": "u1", "username": "alice", "role_keys": ["super_admin"]})
        payload = auth.verify_token(token)
        assert "role_keys" not in payload
        assert "roles" not in payload

    def test_adversarial_tampered_token_fails_verification(self, monkeypatch):
        monkeypatch.setattr(auth, "_jwt_secret", lambda: "test-secret")
        token = auth.create_token({"id": "u1", "username": "alice"})
        tampered = token[:-4] + "abcd"
        with pytest.raises(pyjwt.InvalidTokenError):
            auth.verify_token(tampered)

    def test_adversarial_expired_token_fails_verification(self, monkeypatch):
        monkeypatch.setattr(auth, "_jwt_secret", lambda: "test-secret")
        now = datetime.datetime.now(datetime.timezone.utc)
        expired_payload = {
            "sub": "u1", "username": "alice",
            "iat": now - datetime.timedelta(hours=9),
            "exp": now - datetime.timedelta(hours=1),
        }
        token = pyjwt.encode(expired_payload, "test-secret", algorithm="HS256")
        with pytest.raises(pyjwt.InvalidTokenError):
            auth.verify_token(token)

    def test_adversarial_wrong_secret_fails_verification(self, monkeypatch):
        monkeypatch.setattr(auth, "_jwt_secret", lambda: "secret-a")
        token = auth.create_token({"id": "u1", "username": "alice"})
        monkeypatch.setattr(auth, "_jwt_secret", lambda: "secret-b")
        with pytest.raises(pyjwt.InvalidTokenError):
            auth.verify_token(token)

    def test_adversarial_missing_jwt_secret_raises_clear_error(self, monkeypatch):
        monkeypatch.setattr(auth, "get_secret", lambda name: None)
        with pytest.raises(RuntimeError, match="JWT_SECRET"):
            auth.create_token({"id": "u1", "username": "alice"})


class TestPermissionCatalog:
    def test_golden_every_default_role_only_grants_real_permissions(self):
        for role in auth.DEFAULT_ROLES:
            unknown = set(role["permission_ids"]) - set(auth.PERMISSION_IDS)
            assert not unknown, f"{role['role_key']} grants unknown permission(s): {unknown}"

    def test_golden_admin_has_every_permission(self):
        """Admin is the top-level role in this app -- there is no
        separate Super Admin tier, so Admin holds the full catalog,
        including roles.create/roles.update and screening.approve."""
        admin = next(r for r in auth.DEFAULT_ROLES if r["role_key"] == "admin")
        assert set(admin["permission_ids"]) == set(auth.PERMISSION_IDS)

    def test_golden_there_is_exactly_one_top_level_role(self):
        """No separate 'super_admin' role exists -- Admin alone has
        roles.create/roles.update/screening.approve."""
        role_keys = {r["role_key"] for r in auth.DEFAULT_ROLES}
        assert "super_admin" not in role_keys
        for role in auth.DEFAULT_ROLES:
            if role["role_key"] == "admin":
                continue
            assert "roles.create" not in role["permission_ids"]
            assert "roles.update" not in role["permission_ids"]

    def test_golden_recruiter_can_propose_but_not_approve_bulk_decisions(self):
        recruiter = next(r for r in auth.DEFAULT_ROLES if r["role_key"] == "recruiter")
        assert "screening.propose" in recruiter["permission_ids"]
        assert "screening.approve" not in recruiter["permission_ids"]

    def test_golden_candidate_role_has_no_admin_or_recruiter_permissions(self):
        """Explicit spec requirement: never make Candidate a role that can
        access recruiter or administrative interfaces."""
        candidate = next(r for r in auth.DEFAULT_ROLES if r["role_key"] == "candidate")
        assert candidate["permission_ids"] == []

    def test_golden_every_permission_has_a_module_label(self):
        for pid, meta in auth.PERMISSIONS.items():
            assert meta.get("module"), f"{pid} is missing a module grouping"
            assert meta.get("label")


class TestEffectivePermissions:
    def test_golden_unions_across_multiple_roles(self):
        roles = [
            {"permission_ids": ["candidates.read"]},
            {"permission_ids": ["interviews.read", "interviews.manage"]},
        ]
        assert auth.effective_permissions(roles) == {"candidates.read", "interviews.read", "interviews.manage"}

    def test_edge_no_roles_grants_nothing(self):
        assert auth.effective_permissions([]) == set()

    def test_edge_role_missing_permission_ids_key_grants_nothing_not_an_error(self):
        assert auth.effective_permissions([{"role_key": "weird"}]) == set()
