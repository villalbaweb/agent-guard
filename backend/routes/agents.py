"""
backend/routes/agents.py — Agent Registry REST API  (U-09 / U-12)
------------------------------------------------------------------
Provides CRUD operations and vector-similarity search for the agent registry.

Endpoints
---------
POST   /agents/register          Register or update an agent
GET    /agents                   List all active agents
GET    /agents/{id}              Get a single agent by ID
DELETE /agents/{id}              Soft-deactivate an agent
POST   /agents/{id}/activate     Re-activate a deactivated agent
POST   /agents/search            Search agents by natural-language intent
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from agentguard.registry import Registry
from ..dependencies import get_registry, require_auth
from ..schemas import (
    AgentListResponse,
    AgentRegisterRequest,
    AgentRegisterResponse,
    AgentSearchRequest,
    AgentSearchResponse,
)

router = APIRouter(prefix="/agents", tags=["agents"])


def _to_response(record: dict) -> AgentRegisterResponse:
    """Convert a registry dict to AgentRegisterResponse, filling missing fields with defaults."""
    return AgentRegisterResponse(
        id=record["id"],
        role=record["role"],
        semantic_description=record["semantic_description"],
        input_schema=record.get("input_schema", {}),
        output_schema=record.get("output_schema", {}),
        endpoint=record.get("endpoint", ""),
        is_active=record.get("is_active", True),
        has_embedding=record.get("has_embedding", False),
        health_status=record.get("health_status", "unknown"),
        last_heartbeat=record.get("last_heartbeat"),
        registered_at=record.get("registered_at"),
        updated_at=record.get("updated_at"),
    )


# --------------------------------------------------------------------------- #
#  POST /agents/register                                                       #
# --------------------------------------------------------------------------- #

@router.post(
    "/register",
    response_model=AgentRegisterResponse,
    status_code=status.HTTP_200_OK,
    summary="Register or update an agent",
)
def register_agent(
    body: AgentRegisterRequest,
    registry: Annotated[Registry, Depends(get_registry)],
    _auth=Depends(require_auth),
) -> AgentRegisterResponse:
    """Register a new agent or update an existing one (upsert by id)."""
    record = registry.register(
        item_id=body.id,
        role=body.role,
        semantic_description=body.semantic_description,
        input_schema=body.input_schema,
        output_schema=body.output_schema,
        endpoint=body.endpoint,
    )
    return _to_response(record)


# --------------------------------------------------------------------------- #
#  GET /agents                                                                 #
# --------------------------------------------------------------------------- #

@router.get(
    "",
    response_model=AgentListResponse,
    summary="List all active agents",
)
def list_agents(
    registry: Annotated[Registry, Depends(get_registry)],
    _auth=Depends(require_auth),
) -> AgentListResponse:
    """Return all currently active agents."""
    agents = registry.list_all(include_inactive=False)
    return AgentListResponse(
        agents=[_to_response(a) for a in agents],
        total=len(agents),
    )


# --------------------------------------------------------------------------- #
#  GET /agents/{id}                                                            #
# --------------------------------------------------------------------------- #

@router.get(
    "/{agent_id}",
    response_model=AgentRegisterResponse,
    summary="Get a single agent by ID",
)
def get_agent(
    agent_id: str,
    registry: Annotated[Registry, Depends(get_registry)],
    _auth=Depends(require_auth),
) -> AgentRegisterResponse:
    """Retrieve an active agent by its stable ID."""
    agent = registry.get(agent_id)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_id}' not found or is inactive.",
        )
    return _to_response(agent)


# --------------------------------------------------------------------------- #
#  DELETE /agents/{id}                                                         #
# --------------------------------------------------------------------------- #

@router.delete(
    "/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-deactivate an agent",
)
def deactivate_agent(
    agent_id: str,
    registry: Annotated[Registry, Depends(get_registry)],
    _auth=Depends(require_auth),
) -> None:
    """Deactivate an agent (soft-delete).  The agent is excluded from search
    results but retained in the database for auditing."""
    found = registry.deactivate(agent_id)
    if not found:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_id}' not found.",
        )


# --------------------------------------------------------------------------- #
#  POST /agents/{id}/activate                                                  #
# --------------------------------------------------------------------------- #

@router.post(
    "/{agent_id}/activate",
    response_model=AgentRegisterResponse,
    summary="Re-activate a deactivated agent",
)
def activate_agent(
    agent_id: str,
    registry: Annotated[Registry, Depends(get_registry)],
    _auth=Depends(require_auth),
) -> AgentRegisterResponse:
    """Re-activate a previously soft-deleted agent."""
    found = registry.activate(agent_id)
    if not found:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_id}' not found.",
        )
    agent = registry.get(agent_id)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_id}' not found after activation.",
        )
    return _to_response(agent)


# --------------------------------------------------------------------------- #
#  POST /agents/search                                                         #
# --------------------------------------------------------------------------- #

@router.post(
    "/search",
    response_model=AgentSearchResponse,
    summary="Search agents by natural-language intent",
)
def search_agents(
    body: AgentSearchRequest,
    registry: Annotated[Registry, Depends(get_registry)],
    _auth=Depends(require_auth),
) -> AgentSearchResponse:
    """Search for agents using vector cosine similarity (or substring fallback)."""
    results = registry.search_by_intent(body.intent, limit=body.limit)

    # Determine which search method was actually used
    if registry._db is not None:
        # Check if any result has an embedding (indicates vector search was used)
        search_method = "vector_similarity" if any(
            r.get("has_embedding") for r in results
        ) else "substring_match"
    else:
        search_method = "in_memory"

    return AgentSearchResponse(
        results=[_to_response(r) for r in results],
        search_method=search_method,
    )
