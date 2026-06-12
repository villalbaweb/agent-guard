"""
dependencies.py — FastAPI Dependency Injection
------------------------------------------------
Provides shared singletons (executor, policy engine, memory, run store) and
the JWT bearer token extractor used by protected endpoints.

Authentication tiers:
  Phase 1 (current): static AGENTGUARD_API_KEY header OR JWT bearer token.
  Phase 5 (future):  RS256 with JWKS endpoint.

Fail-closed: any missing or invalid credential returns 401 before the
executor is touched.
"""
import os
import logging
from functools import lru_cache
from typing import Optional, Annotated

from fastapi import Depends, Header, HTTPException, status

from agentguard.db import DatabaseManager
from agentguard.memory import MemoryManager
from agentguard.policy import PolicyEngine
from agentguard.registry import Registry
from agentguard.executor import RecursiveExecutor
from agentguard.auth import AuthContext, decode_token, make_auth_context
from .store import RunStore

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
#  Singletons                                                                  #
# --------------------------------------------------------------------------- #

@lru_cache(maxsize=1)
def get_memory() -> MemoryManager:
    return MemoryManager()


@lru_cache(maxsize=1)
def get_database() -> DatabaseManager:
    """Singleton PostgreSQL connection pool. Returns an unavailable manager
    (available=False) when DATABASE_URL is not set — callers degrade gracefully."""
    return DatabaseManager()


@lru_cache(maxsize=1)
def get_registry() -> Registry:
    """Singleton agent registry — backed by PostgreSQL when available.

    Seeds two default agents on first call if the registry is empty.
    NOTE: @lru_cache(maxsize=1) ensures the executor, planner, and REST routes
    all share the same instance with hot in-memory data.
    """
    registry = Registry(db=get_database())

    # Seed default agents only when the registry is empty.
    # In PostgreSQL mode this avoids re-inserting agents on every restart.
    if not registry.list_all():
        registry.register(
            item_id="search_agent_01",
            role="Search Specialist",
            semantic_description="Searches the web for any topic or query.",
            input_schema={"query": "string"},
            output_schema={"results": "list"},
        )
        registry.register(
            item_id="analysis_agent_01",
            role="Data Analyst",
            semantic_description="Analyzes datasets and provides structured insights.",
            input_schema={"data": "string"},
            output_schema={"analysis": "string"},
        )
    return registry


@lru_cache(maxsize=1)
def get_policy_engine() -> PolicyEngine:
    """Singleton PolicyEngine shared by every executor and the /policy routes.

    Sharing one instance means POST /policy/reload actually affects subsequent
    runs and circuit-breaker state persists across requests.
    """
    return PolicyEngine(get_memory())


def get_executor(
    memory: Annotated[MemoryManager, Depends(get_memory)],
    registry: Annotated[Registry, Depends(get_registry)],
) -> RecursiveExecutor:
    """Fresh executor per-request, wired to the shared checkpointer + policy engine."""
    return RecursiveExecutor(
        memory_manager=memory,
        registry=registry,
        max_depth=3,
        checkpointer=get_checkpointer(),
        policy_engine=get_policy_engine(),
    )


def get_run_store(
    memory: Annotated[MemoryManager, Depends(get_memory)],
) -> RunStore:
    return RunStore(memory)


@lru_cache(maxsize=1)
def get_checkpointer():
    """Singleton checkpointer: Redis-backed when available, else MemorySaver.

    Must be a process-wide singleton — a per-request MemorySaver would lose the
    checkpoint between POST /runs and POST /runs/{id}/approve, making HITL
    resume impossible.
    """
    try:
        from langgraph.checkpoint.memory import MemorySaver
    except Exception as e:
        logger.warning(f"Checkpointer unavailable: {e}. HITL interrupt/resume disabled.")
        return None

    # Try Redis-backed checkpointer if the package is installed and reachable
    try:
        from langgraph.checkpoint.redis import RedisSaver
        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        # from_conn_string returns a context manager in recent versions;
        # enter it once and keep the saver alive for the process lifetime.
        cm_or_saver = RedisSaver.from_conn_string(redis_url)
        saver = cm_or_saver.__enter__() if hasattr(cm_or_saver, "__enter__") and not hasattr(cm_or_saver, "get_tuple") else cm_or_saver
        if hasattr(saver, "setup"):
            saver.setup()
        logger.info("Checkpointer: Redis-backed (HITL survives restarts)")
        return saver
    except ImportError:
        logger.info("Checkpointer: MemorySaver (install langgraph-checkpoint-redis for Redis)")
    except Exception as e:
        logger.warning(f"Checkpointer: Redis saver failed ({e}) — falling back to MemorySaver.")
    return MemorySaver()


# --------------------------------------------------------------------------- #
#  Authentication                                                              #
# --------------------------------------------------------------------------- #

def _static_api_key() -> Optional[str]:
    return os.environ.get("AGENTGUARD_API_KEY")


def require_auth(
    authorization: Annotated[Optional[str], Header()] = None,
    x_api_key: Annotated[Optional[str], Header(alias="x-api-key")] = None,
) -> AuthContext:
    """
    Extract and validate credentials from the request.

    Accepts either:
      - Bearer <JWT>  in the Authorization header, OR
      - Static key    in the X-Api-Key header (AGENTGUARD_API_KEY env var).

    Returns:
        An AuthContext with the caller's identity.

    Raises:
        HTTPException(401): on missing or invalid credentials.
    """
    # Static API key (Phase 1)
    static_key = _static_api_key()
    if static_key and x_api_key == static_key:
        return make_auth_context(subject="api-key-user", roles=["user", "approver"])

    # JWT bearer token
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
        if token:  # skip if token is empty (e.g. frontend sends "Bearer " with no value)
            try:
                ctx = decode_token(token)
            except ValueError as e:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=str(e),
                    headers={"WWW-Authenticate": "Bearer"},
                )
            if ctx.is_expired():
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="JWT has expired.",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            return ctx

    # No credential provided — check if auth is disabled for development
    if os.environ.get("AGENTGUARD_NO_AUTH", "").lower() in ("1", "true", "yes"):
        logger.warning("Auth disabled via AGENTGUARD_NO_AUTH — using anonymous context.")
        from agentguard.auth import anonymous_context
        return anonymous_context()

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing credentials. Provide Bearer token or X-Api-Key header.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_approver(auth: Annotated[AuthContext, Depends(require_auth)]) -> AuthContext:
    """Guard that enforces the 'approver' role for HITL approval endpoints."""
    if not auth.has_role("approver"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Role 'approver' required. Caller has roles: {auth.roles}.",
        )
    return auth
