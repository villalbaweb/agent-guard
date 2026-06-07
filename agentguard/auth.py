"""
auth.py — JWT Generation, Verification, and AuthContext
---------------------------------------------------------
Provides the AuthContext dataclass (threaded through every AgentGuard run)
and HS256-based JWT encode/decode for development.

Algorithm selection:
  - Development: HS256 symmetric key via AGENTGUARD_JWT_SECRET env var.
  - Production:  RS256 with JWKS endpoint (Phase 5 — deferred).

Fail-closed defaults:
  - Missing or invalid JWT → 401 before touching the executor.
  - Expired auth_context → step blocked with reason "auth_context expired".
  - Missing AGENTGUARD_JWT_SECRET → RuntimeError at token creation time.
"""
import os
import uuid
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

try:
    import jwt as pyjwt
    _JWT_AVAILABLE = True
except ImportError:
    _JWT_AVAILABLE = False
    logger.warning("PyJWT not installed — JWT auth is disabled. Install pyjwt>=2.9.0.")


# --------------------------------------------------------------------------- #
#  AuthContext                                                                 #
# --------------------------------------------------------------------------- #

@dataclass
class AuthContext:
    """Verifiable caller identity threaded through every subgraph execution.

    Fields map 1-to-1 with JWT standard claims:
        subject    → sub
        tenant_id  → tenant
        roles      → roles
        issued_at  → iat
        expires_at → exp
        token_id   → jti
    """
    subject: str                     # stable user/service id (e.g. "user@example.com")
    tenant_id: str                   # multi-tenancy isolation key
    roles: List[str]                 # e.g. ["user", "approver", "admin"]
    issued_at: datetime
    expires_at: datetime
    token_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def is_expired(self) -> bool:
        """Return True if the token expiry has passed."""
        return datetime.now(timezone.utc) >= self.expires_at

    def has_role(self, role: str) -> bool:
        """Return True if this context carries the requested role."""
        return role in self.roles

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dict suitable for storing in AgentGuardState."""
        return {
            "subject": self.subject,
            "tenant_id": self.tenant_id,
            "roles": self.roles,
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "token_id": self.token_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AuthContext":
        """Reconstruct from a state dict (reverse of to_dict)."""
        return cls(
            subject=data["subject"],
            tenant_id=data.get("tenant_id", "default"),
            roles=data.get("roles", []),
            issued_at=datetime.fromisoformat(data["issued_at"]),
            expires_at=datetime.fromisoformat(data["expires_at"]),
            token_id=data.get("token_id", str(uuid.uuid4())),
        )


# --------------------------------------------------------------------------- #
#  Token helpers                                                               #
# --------------------------------------------------------------------------- #

def _get_secret() -> str:
    secret = os.environ.get("AGENTGUARD_JWT_SECRET", "")
    if not secret:
        raise ValueError(
            "AGENTGUARD_JWT_SECRET environment variable is not set. "
            "Generate a secret with: python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    return secret


def _get_issuer() -> str:
    return os.environ.get("AGENTGUARD_JWT_ISSUER", "agentguard")


def create_token(auth_context: AuthContext) -> str:
    """Encode an AuthContext as a signed HS256 JWT string.

    Raises:
        RuntimeError: if PyJWT is not installed.
        ValueError:   if AGENTGUARD_JWT_SECRET is not set.
    """
    if not _JWT_AVAILABLE:
        raise RuntimeError("PyJWT is not installed. Run: uv add pyjwt>=2.9.0")

    payload = {
        "sub": auth_context.subject,
        "tenant": auth_context.tenant_id,
        "roles": auth_context.roles,
        "iat": int(auth_context.issued_at.timestamp()),
        "exp": int(auth_context.expires_at.timestamp()),
        "jti": auth_context.token_id,
        "iss": _get_issuer(),
    }
    return pyjwt.encode(payload, _get_secret(), algorithm="HS256")


def _get_jwks_public_key(token: str):
    """Fetch and cache the matching public key from a JWKS endpoint.

    Reads AGENTGUARD_JWKS_URL from the environment.  On first call the JWKS
    document is fetched and cached in-process; subsequent calls reuse it.
    Returns a key object suitable for pyjwt.decode(), or None if unavailable.

    This implements the P-01 (JWKS / RS256) learning objective: understand how
    OIDC identity providers (Okta, Auth0, Azure AD) issue tokens that a
    relying-party service can validate without sharing a secret.
    """
    jwks_url = os.environ.get("AGENTGUARD_JWKS_URL", "")
    if not jwks_url:
        return None

    # Decode header to find the key ID (kid)
    try:
        unverified_header = pyjwt.get_unverified_header(token)
    except Exception:
        return None
    kid = unverified_header.get("kid")

    global _jwks_cache
    if _jwks_cache is None:
        try:
            import urllib.request
            with urllib.request.urlopen(jwks_url, timeout=5) as resp:
                import json as _json
                _jwks_cache = _json.loads(resp.read())
            logger.info(f"auth: fetched JWKS from {jwks_url}")
        except Exception as e:
            logger.error(f"auth: failed to fetch JWKS from {jwks_url}: {e}")
            return None

    try:
        from jwt.algorithms import RSAAlgorithm
        for key_data in _jwks_cache.get("keys", []):
            if kid is None or key_data.get("kid") == kid:
                import json as _json
                return RSAAlgorithm.from_jwk(_json.dumps(key_data))
    except Exception as e:
        logger.error(f"auth: JWKS key extraction failed: {e}")

    return None


_jwks_cache = None   # module-level cache; reset by tests or on process restart


def decode_token(token: str) -> AuthContext:
    """Verify and decode a JWT, returning an AuthContext.

    Algorithm selection (P-01):
      - If AGENTGUARD_JWKS_URL is set → RS256 via public key from JWKS endpoint.
      - Otherwise → HS256 via AGENTGUARD_JWT_SECRET (development / self-hosted).

    Raises:
        RuntimeError: if PyJWT is not installed.
        ValueError:   on any verification failure (expired, bad sig, wrong issuer).
    """
    if not _JWT_AVAILABLE:
        raise RuntimeError("PyJWT is not installed. Run: uv add pyjwt>=2.9.0")

    jwks_key = _get_jwks_public_key(token) if _JWT_AVAILABLE else None

    try:
        if jwks_key is not None:
            payload = pyjwt.decode(
                token,
                jwks_key,
                algorithms=["RS256"],
                options={"require": ["sub", "exp", "iat", "jti"]},
            )
        else:
            payload = pyjwt.decode(
                token,
                _get_secret(),
                algorithms=["HS256"],
                issuer=_get_issuer(),
                options={"require": ["sub", "exp", "iat", "jti"]},
            )
    except pyjwt.ExpiredSignatureError:
        raise ValueError("JWT has expired.")
    except pyjwt.InvalidIssuerError:
        raise ValueError("JWT issuer mismatch.")
    except pyjwt.InvalidSignatureError:
        raise ValueError("JWT signature verification failed.")
    except pyjwt.DecodeError as e:
        raise ValueError(f"JWT decode error: {e}")

    return AuthContext(
        subject=payload["sub"],
        tenant_id=payload.get("tenant", "default"),
        roles=payload.get("roles", []),
        issued_at=datetime.fromtimestamp(payload["iat"], tz=timezone.utc),
        expires_at=datetime.fromtimestamp(payload["exp"], tz=timezone.utc),
        token_id=payload["jti"],
    )


def make_auth_context(
    subject: str,
    tenant_id: str = "default",
    roles: Optional[List[str]] = None,
    ttl_seconds: int = 3600,
) -> AuthContext:
    """Convenience factory for tests and API token issuance.

    Args:
        subject:     Caller identity (e.g. "user@example.com" or "service-xyz").
        tenant_id:   Tenant namespace.
        roles:       List of roles granted to this caller.
        ttl_seconds: Token lifetime in seconds (default 1 hour).

    Returns:
        A fresh AuthContext with issued_at=now and expires_at=now+ttl.
    """
    now = datetime.now(timezone.utc)
    return AuthContext(
        subject=subject,
        tenant_id=tenant_id,
        roles=roles or ["user"],
        issued_at=now,
        expires_at=now + timedelta(seconds=ttl_seconds),
    )


# --------------------------------------------------------------------------- #
#  Anonymous sentinel (used when no JWT is present in PoC mode)               #
# --------------------------------------------------------------------------- #

def anonymous_context() -> AuthContext:
    """Return a placeholder AuthContext for PoC / AGENTGUARD_NO_AUTH dev mode.

    Includes all roles so that every endpoint is exercisable without a real JWT.
    This context is intentionally permissive — never use in production.
    """
    now = datetime.now(timezone.utc)
    return AuthContext(
        subject="anonymous",
        tenant_id="default",
        roles=["user", "approver", "admin"],
        issued_at=now,
        expires_at=now + timedelta(hours=24),
    )
