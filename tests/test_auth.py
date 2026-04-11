"""
tests/test_auth.py — Unit tests for agentguard.auth

Coverage:
  - AuthContext.to_dict() / from_dict() round-trip
  - AuthContext.is_expired() and has_role()
  - JWT encode/decode happy path
  - Expired JWT raises ValueError
  - Bad signature raises ValueError
  - Missing claims raises ValueError
  - anonymous_context() returns non-expired context
  - make_auth_context() with custom TTL
"""
import os
import time
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from agentguard.auth import (
    AuthContext, create_token, decode_token,
    make_auth_context, anonymous_context,
)

# ------------------------------------------------------------------ #
#  Helpers                                                             #
# ------------------------------------------------------------------ #

os.environ.setdefault("AGENTGUARD_JWT_SECRET", "test-secret-for-unit-tests-only")
os.environ.setdefault("AGENTGUARD_JWT_ISSUER", "agentguard")


def _fresh_context(**kwargs) -> AuthContext:
    defaults = dict(
        subject="user@example.com",
        tenant_id="acme",
        roles=["user"],
        ttl_seconds=3600,
    )
    defaults.update(kwargs)
    return make_auth_context(**defaults)


# ------------------------------------------------------------------ #
#  AuthContext dataclass                                               #
# ------------------------------------------------------------------ #

class TestAuthContext:
    def test_to_dict_keys(self):
        ctx = _fresh_context()
        d = ctx.to_dict()
        assert set(d.keys()) == {"subject", "tenant_id", "roles", "issued_at", "expires_at", "token_id"}

    def test_round_trip(self):
        ctx = _fresh_context(subject="svc-abc", roles=["admin"])
        restored = AuthContext.from_dict(ctx.to_dict())
        assert restored.subject == "svc-abc"
        assert restored.roles == ["admin"]
        assert restored.tenant_id == ctx.tenant_id
        assert restored.token_id == ctx.token_id

    def test_is_expired_false_for_fresh(self):
        ctx = _fresh_context(ttl_seconds=3600)
        assert not ctx.is_expired()

    def test_is_expired_true_for_past(self):
        now = datetime.now(timezone.utc)
        ctx = AuthContext(
            subject="x", tenant_id="t", roles=["user"],
            issued_at=now - timedelta(hours=2),
            expires_at=now - timedelta(hours=1),
        )
        assert ctx.is_expired()

    def test_has_role_true(self):
        ctx = _fresh_context(roles=["user", "approver"])
        assert ctx.has_role("approver")

    def test_has_role_false(self):
        ctx = _fresh_context(roles=["user"])
        assert not ctx.has_role("admin")


# ------------------------------------------------------------------ #
#  JWT encode / decode                                                 #
# ------------------------------------------------------------------ #

class TestJWT:
    def test_encode_decode_round_trip(self):
        ctx = _fresh_context(subject="alice", roles=["user", "approver"])
        token = create_token(ctx)
        assert isinstance(token, str)
        decoded = decode_token(token)
        assert decoded.subject == "alice"
        assert "approver" in decoded.roles
        assert decoded.tenant_id == ctx.tenant_id
        assert decoded.token_id == ctx.token_id

    def test_expired_token_raises(self):
        now = datetime.now(timezone.utc)
        ctx = AuthContext(
            subject="bob", tenant_id="t", roles=["user"],
            issued_at=now - timedelta(hours=2),
            expires_at=now - timedelta(hours=1),
        )
        token = create_token(ctx)
        with pytest.raises(ValueError, match="expired"):
            decode_token(token)

    def test_bad_signature_raises(self):
        ctx = _fresh_context()
        token = create_token(ctx)
        # Tamper with the signature
        tampered = token[:-4] + "XXXX"
        with pytest.raises(ValueError):
            decode_token(tampered)

    def test_malformed_token_raises(self):
        with pytest.raises(ValueError):
            decode_token("not.a.valid.jwt")

    def test_wrong_issuer_raises(self):
        ctx = _fresh_context()
        token = create_token(ctx)
        with patch.dict(os.environ, {"AGENTGUARD_JWT_ISSUER": "wrong-issuer"}):
            with pytest.raises(ValueError):
                decode_token(token)

    def test_missing_secret_raises(self):
        with patch.dict(os.environ, {"AGENTGUARD_JWT_SECRET": ""}):
            with pytest.raises(ValueError, match="AGENTGUARD_JWT_SECRET"):
                create_token(_fresh_context())


# ------------------------------------------------------------------ #
#  Factory helpers                                                     #
# ------------------------------------------------------------------ #

class TestFactories:
    def test_make_auth_context_defaults(self):
        ctx = make_auth_context("svc")
        assert ctx.subject == "svc"
        assert ctx.tenant_id == "default"
        assert ctx.roles == ["user"]
        assert not ctx.is_expired()

    def test_make_auth_context_custom_ttl(self):
        ctx = make_auth_context("svc", ttl_seconds=60)
        delta = ctx.expires_at - ctx.issued_at
        assert 59 <= delta.total_seconds() <= 61

    def test_anonymous_context_not_expired(self):
        ctx = anonymous_context()
        assert ctx.subject == "anonymous"
        assert not ctx.is_expired()

    def test_anonymous_context_has_user_role(self):
        ctx = anonymous_context()
        assert ctx.has_role("user")
