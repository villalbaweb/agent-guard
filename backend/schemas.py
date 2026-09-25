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
    policy_id: Optional[str] = Field(None, description="The distinct policy ID/tenant to apply to this run.")
    callback_url: Optional[str] = Field(
        None,
        description=(
            "D-02: Optional webhook URL. On run completion/block/error, AgentGuard POSTs "
            "the RunStatusResponse payload here. Demonstrates the async long-running-job pattern."
        ),
    )


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
    postgres: Literal["ok", "unavailable"]   # U-09/U-12
    llm: Literal["ok", "unavailable"]
    llm_provider: Optional[str] = Field(
        None, description="Active chat provider: google | openrouter | anthropic | openai | vertex."
    )
    jev_use_cases: List[str] = Field(
        default_factory=list,
        description="Decision points answered by Jev instead of the chat model: guard | reflect | route.",
    )
    timestamp: datetime


# --------------------------------------------------------------------------- #
#  Agent Registry Models  (U-09 / U-12)                                       #
# --------------------------------------------------------------------------- #

class AgentRegisterRequest(BaseModel):
    id: str = Field(
        ...,
        description="Stable unique agent identifier.",
        min_length=1,
        max_length=128,
    )
    role: str = Field(..., description="Human-readable capability label.")
    semantic_description: str = Field(
        ..., description="Prose description for intent routing."
    )
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    output_schema: Dict[str, Any] = Field(default_factory=dict)
    endpoint: str = Field("", description="Optional HTTP endpoint for remote agents.")


class AgentRegisterResponse(BaseModel):
    id: str
    role: str
    semantic_description: str
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]
    endpoint: str
    is_active: bool
    has_embedding: bool
    health_status: str = "unknown"
    last_heartbeat: Optional[datetime] = None
    registered_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class AgentSearchRequest(BaseModel):
    intent: str = Field(
        ..., description="Natural-language intent to match against agent descriptions."
    )
    limit: int = Field(5, ge=1, le=50, description="Maximum number of results.")


class AgentSearchResponse(BaseModel):
    results: List[AgentRegisterResponse]
    search_method: str  # "vector_similarity" | "substring_match" | "in_memory"


class AgentListResponse(BaseModel):
    agents: List[AgentRegisterResponse]
    total: int


# --------------------------------------------------------------------------- #
#  POST /api/gatekeeper/*                                                      #
# --------------------------------------------------------------------------- #
#  Inline governance for agents that run in their own process (a separate
#  service, a different framework) instead of inside RecursiveExecutor. They
#  call these endpoints per step; the same shared PolicyEngine decides.

class VerifyRequest(BaseModel):
    agent_id: str = Field(..., description="Identity of the calling agent/node, used as the policy action.")
    input_text: str = Field(..., description="The text to evaluate — the prompt, task, or tool input.")
    context: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Optional evaluation context. Recognised keys: run_id/task_id (groups "
            "steps for loop detection), depth, cost_estimate, max_cost, policy_id, "
            "and stage ('preflight' to run the task-level check instead of a step check)."
        ),
    )


class VerifyResponse(BaseModel):
    outcome: Literal["ALLOW", "BLOCK", "PAUSE"] = Field(
        ..., description="PAUSE means a require_hitl rule matched — hold for human approval."
    )
    reason: str
    decision_id: str
    run_id: Optional[str] = None


class ConsumeRequest(BaseModel):
    run_id: str
    cost: float = Field(0.0, ge=0.0, description="Cost in USD incurred by the step being reported.")
    steps: int = Field(1, ge=0)
    depth: int = Field(0, ge=0)
    max_cost: Optional[float] = Field(None, description="Per-run budget override; falls back to policy.yaml.")
    max_depth: Optional[int] = Field(None, description="Per-run recursion ceiling; unlimited when omitted.")


class ConsumeResponse(BaseModel):
    status: Literal["ALLOWED", "BLOCKED"]
    reason: str
    usage: Dict[str, float] = Field(default_factory=dict)


class TrackThoughtRequest(BaseModel):
    run_id: str
    node_name: str
    text: str


class TrackThoughtResponse(BaseModel):
    status: Literal["recorded"]
    history_size: int


class ClearHistoryResponse(BaseModel):
    status: Literal["cleared"]
    run_id: str
    dropped: int


# --------------------------------------------------------------------------- #
#  Error envelope                                                              #
# --------------------------------------------------------------------------- #

class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Optional[Any] = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
