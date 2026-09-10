"""
POST /api/gatekeeper/verify              — policy check for a single agent step
POST /api/gatekeeper/consume             — report usage; trips the budget breaker
POST /api/gatekeeper/track_thought       — feed the loop detector
POST /api/gatekeeper/clear-history/{id}  — reset a run's thought history

Inline governance for agents that live outside RecursiveExecutor.

The /runs API governs graphs AgentGuard executes itself. These endpoints cover
the other case: an agent running in its own process that wants the same
PolicyEngine to approve each step before it takes it. Both paths share the
singleton engine and MemoryManager, so budgets, content rules, loop detection,
and POST /policy/reload apply identically no matter who is asking.

Decisions map onto three outcomes:
  ALLOW — proceed
  BLOCK — refuse; the caller is expected to stop
  PAUSE — a require_hitl rule matched; hold for human approval
"""
import logging
import uuid
from typing import Annotated, Any, Dict, Optional

from fastapi import APIRouter, Depends, Response, status

from agentguard.auth import AuthContext
from agentguard.state import make_initial_state
from ..dependencies import get_memory, get_policy_engine, require_auth
from ..schemas import (
    ClearHistoryResponse,
    ConsumeRequest,
    ConsumeResponse,
    TrackThoughtRequest,
    TrackThoughtResponse,
    VerifyRequest,
    VerifyResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/gatekeeper", tags=["gatekeeper"])


def _run_id_from(context: Dict[str, Any]) -> str:
    """Callers name the run inconsistently; accept the common spellings.

    The run id is what groups steps together for loop detection and budget
    accounting, so falling back to a shared constant would let unrelated
    agents contaminate each other's history. Mint a unique id instead.
    """
    for key in ("run_id", "task_id", "root_task_id", "thread_id"):
        value = context.get(key)
        if value:
            return str(value)
    return f"ungrouped-{uuid.uuid4()}"


@router.post("/verify", response_model=VerifyResponse, summary="Authorize a single agent step")
def verify(
    body: VerifyRequest,
    auth: Annotated[AuthContext, Depends(require_auth)],
) -> VerifyResponse:
    """Evaluate one step against the active policy."""
    engine = get_policy_engine()
    memory = get_memory()

    context = body.context or {}
    run_id = _run_id_from(context)

    budget_config: Dict[str, float] = {}
    if context.get("max_cost") is not None:
        budget_config["max_cost_usd"] = float(context["max_cost"])
    if context.get("per_step_limit") is not None:
        budget_config["per_step_limit_usd"] = float(context["per_step_limit"])

    state = make_initial_state(
        task=body.input_text,
        subject=auth.subject,
        root_task_id=run_id,
        budget_config=budget_config,
        auth_context=auth.to_dict(),
        depth=int(context.get("depth") or 0),
        policy_id=context.get("policy_id"),
    )
    # Seed the cumulative spend reported via /consume so the budget check in
    # check_step sees the run's real total, not a fresh 0.0 on every call.
    state["usage_stats"] = {"total_cost": memory.get_consumption(run_id).get("total_cost", 0.0)}

    if context.get("stage") == "preflight":
        allowed, decision = engine.check_preflight(state)
    else:
        allowed, decision = engine.check_step(
            state,
            action=body.agent_id,
            intent=body.input_text,
            cost_estimate=float(context.get("cost_estimate") or 0.0),
        )

    if allowed:
        outcome = "ALLOW"
    elif decision.get("hitl_required"):
        outcome = "PAUSE"
    else:
        outcome = "BLOCK"

    if outcome != "ALLOW":
        logger.info(f"Gatekeeper: {outcome} {body.agent_id} (run {run_id}) — {decision.get('reason')}")

    return VerifyResponse(
        outcome=outcome,
        reason=decision.get("reason", ""),
        decision_id=str(uuid.uuid4()),
        run_id=run_id,
    )


@router.post("/consume", response_model=ConsumeResponse, summary="Report usage and check the breaker")
def consume(
    body: ConsumeRequest,
    response: Response,
    auth: Annotated[AuthContext, Depends(require_auth)],
) -> ConsumeResponse:
    """Accumulate a run's spend and trip the circuit breaker when it overruns.

    Returns 429 once a ceiling is crossed so callers can detect the trip from
    the status code alone, without parsing the body.
    """
    engine = get_policy_engine()
    memory = get_memory()

    usage = memory.record_consumption(
        run_id=body.run_id, cost=body.cost, steps=body.steps, depth=body.depth
    )

    max_cost = body.max_cost
    if max_cost is None:
        policy_dict, _ = engine._get_policy()
        max_cost = policy_dict.get("budget", {}).get("max_cost_usd", 10.0)

    reason: Optional[str] = None
    if usage["total_cost"] > max_cost:
        reason = f"Budget exhausted: ${usage['total_cost']:.4f} spent against a ${max_cost:.2f} ceiling."
    elif body.max_depth is not None and body.depth > body.max_depth:
        reason = f"Recursion ceiling exceeded: depth {body.depth} > max_depth {body.max_depth}."

    if reason:
        logger.warning(f"Gatekeeper: circuit breaker tripped for run {body.run_id} — {reason}")
        response.status_code = status.HTTP_429_TOO_MANY_REQUESTS
        return ConsumeResponse(status="BLOCKED", reason=reason, usage=usage)

    return ConsumeResponse(status="ALLOWED", reason="Within budget.", usage=usage)


@router.post("/track_thought", response_model=TrackThoughtResponse, summary="Record a step for loop detection")
def track_thought(
    body: TrackThoughtRequest,
    auth: Annotated[AuthContext, Depends(require_auth)],
) -> TrackThoughtResponse:
    """Append an agent's output to the run history the loop detector reads."""
    memory = get_memory()
    memory.record_thought(body.run_id, {"action": body.node_name, "intent": body.text})
    return TrackThoughtResponse(
        status="recorded", history_size=len(memory.get_history(body.run_id))
    )


@router.post(
    "/clear-history/{run_id}",
    response_model=ClearHistoryResponse,
    summary="Reset a run's thought history",
)
def clear_history(
    run_id: str,
    auth: Annotated[AuthContext, Depends(require_auth)],
) -> ClearHistoryResponse:
    """Drop the recorded thoughts for a run.

    Called after a human interjects: the prior thoughts describe a path the
    operator has just overridden, and leaving them in place would make loop
    detection fire against reasoning that no longer applies.
    """
    dropped = get_memory().clear_history(run_id)
    logger.info(f"Gatekeeper: cleared {dropped} thoughts for run {run_id} (by {auth.subject}).")
    return ClearHistoryResponse(status="cleared", run_id=run_id, dropped=dropped)
