"""
Pydantic request/response models for the AgentGuard REST API.

All models use Pydantic v2 with strict typing.
"""
from datetime import datetime
from typing import Literal, Optional, Dict, Any, List
from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
#  POST /runs                                                                  #
# --------------------------------------------------------------------------- #

class RunCreateRequest(BaseModel):
    task: str = Field(..., description="Natural-language task for the agent graph to execute.")
    subject: str = Field("default", description="Caller identifier (used in audit logs).")
    budget_override: Optional[Dict[str, float]] = Field(
        None, description="Per-run budget overrides, e.g. {\"max_cost_usd\": 1.0}."
    )
    max_depth: int = Field(3, ge=1, le=10, description="Maximum recursion depth.")


class RunCreateResponse(BaseModel):
    run_id: str
    status: Literal["queued", "running"]
    submitted_at: datetime


# --------------------------------------------------------------------------- #
#  GET /runs/{id}                                                              #
# --------------------------------------------------------------------------- #

class RunStatusResponse(BaseModel):
    run_id: str
    status: Literal["queued", "running", "completed", "blocked", "hitl_pending", "error"]
    final_answer: Optional[str] = None
    total_cost_usd: float = 0.0
    decisions_summary: Dict[str, int] = Field(
        default_factory=dict,
        description="Counts per outcome, e.g. {\"allow\": 5, \"block\": 1, \"hitl\": 0}.",
    )
    trace_url: str
    pending_steps: Optional[List[Dict[str, Any]]] = Field(
        None, description="HITL items awaiting approval (populated when status=hitl_pending)."
    )


# --------------------------------------------------------------------------- #
#  POST /runs/{id}/approve                                                     #
# --------------------------------------------------------------------------- #

class ApproveRequest(BaseModel):
    event_id: str = Field(..., description="TraceEvent event_id of the step to approve.")
    approved_by: str = Field(..., description="Subject of the approver (from JWT).")
    decision: Literal["approve", "reject"] = "approve"
    notes: Optional[str] = None


class ApproveResponse(BaseModel):
    run_id: str
    event_id: str
    status: Literal["resumed", "rejected"]
    approved_by: str


# --------------------------------------------------------------------------- #
#  GET /policy                                                                 #
# --------------------------------------------------------------------------- #

class PolicyResponse(BaseModel):
    policy_version: str
    content: Dict[str, Any]


# --------------------------------------------------------------------------- #
#  POST /policy/reload                                                         #
# --------------------------------------------------------------------------- #

class PolicyReloadResponse(BaseModel):
    reloaded: bool
    policy_version: str
    rules_count: int


# --------------------------------------------------------------------------- #
#  GET /health                                                                 #
# --------------------------------------------------------------------------- #

class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "unhealthy"]
    redis: Literal["ok", "unavailable"]
    llm: Literal["ok", "unavailable"]
    timestamp: datetime


# --------------------------------------------------------------------------- #
#  Error envelope                                                              #
# --------------------------------------------------------------------------- #

class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Optional[Any] = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
