"""
GET /policy   — return currently loaded policy.yaml contents
POST /policy/reload — hot-reload policy without restarting
"""
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from agentguard.auth import AuthContext
from ..dependencies import require_auth
from ..schemas import PolicyResponse, PolicyReloadResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/policy", tags=["policy"])

# Shared singleton — loaded once and hot-reloaded by POST /policy/reload
_policy_engine = None
_reload_lock = False


def _get_engine():
    global _policy_engine
    if _policy_engine is None:
        from agentguard.memory import MemoryManager
        from agentguard.policy import PolicyEngine
        _policy_engine = PolicyEngine(MemoryManager())
    return _policy_engine


@router.get("", response_model=PolicyResponse, summary="Return loaded policy")
def get_policy(auth: Annotated[AuthContext, Depends(require_auth)]) -> PolicyResponse:
    """Return the currently active global policy rules."""
    engine = _get_engine()
    policy_dict, _ = engine._get_policy()  # gets global policy
    return PolicyResponse(
        policy_version="1.0",
        content=policy_dict,
    )


@router.post("/reload", response_model=PolicyReloadResponse, summary="Hot-reload policy file")
def reload_policy(auth: Annotated[AuthContext, Depends(require_auth)]) -> PolicyReloadResponse:
    """Reload the global policy.yaml from disk without restarting the server.

    Returns 409 if a reload is already in progress.
    """
    global _reload_lock
    if _reload_lock:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A policy reload is already in progress.",
        )
    _reload_lock = True
    try:
        engine = _get_engine()
        engine.reload()  # Evicts global policy from cache
        policy_dict, compiled_rules = engine._get_policy() # Re-loads from disk
        rules_count = len(compiled_rules)
        logger.info(f"Global policy reloaded by {auth.subject} — {rules_count} rules active.")
        return PolicyReloadResponse(
            reloaded=True,
            policy_version="1.0",
            rules_count=rules_count,
        )
    finally:
        _reload_lock = False
