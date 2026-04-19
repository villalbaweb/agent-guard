"""
runs.py — Run lifecycle endpoints
-----------------------------------
POST   /runs              — submit a new task (background execution)
GET    /runs/{id}         — fetch run status, answer, usage, governance summary
GET    /runs/{id}/trace   — download full causal graph JSON (Step A artifact)
POST   /runs/{id}/approve — approve a HITL_PENDING step; resumes the graph (Step C)
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Annotated, Dict, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from agentguard.state import AgentGuardState, make_initial_state
from agentguard.executor import RecursiveExecutor
from agentguard.auth import AuthContext
from agentguard import trace as tracer
from ..dependencies import get_executor, get_run_store, require_auth, require_approver
from ..schemas import (
    RunCreateRequest, RunCreateResponse,
    RunStatusResponse, ApproveRequest, ApproveResponse,
)
from ..store import RunStore

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/runs", tags=["runs"])


# --------------------------------------------------------------------------- #
#  POST /runs                                                                  #
# --------------------------------------------------------------------------- #

@router.post("", response_model=RunCreateResponse, status_code=status.HTTP_202_ACCEPTED)
def create_run(
    body: RunCreateRequest,
    background_tasks: BackgroundTasks,
    auth: Annotated[AuthContext, Depends(require_auth)],
    executor: Annotated[RecursiveExecutor, Depends(get_executor)],
    store: Annotated[RunStore, Depends(get_run_store)],
) -> RunCreateResponse:
    """Submit a new task.  Returns immediately with run_id; execution is async."""
    run_id = store.new_run_id()
    submitted_at = datetime.now(timezone.utc)

    record = store.create_run(run_id=run_id, task=body.task, subject=auth.subject)
    store.set_status(run_id, "running")

    background_tasks.add_task(
        _execute_run,
        run_id=run_id,
        body=body,
        auth=auth,
        executor=executor,
        store=store,
    )

    return RunCreateResponse(
        run_id=run_id,
        status="running",
        submitted_at=submitted_at,
    )


def _execute_run(
    run_id: str,
    body: RunCreateRequest,
    auth: AuthContext,
    executor: RecursiveExecutor,
    store: RunStore,
):
    """Background task: build graph, invoke, persist state + trace."""
    started_at = tracer._now_iso()
    try:
        initial_state = make_initial_state(
            task=body.task,
            subject=auth.subject,
            root_task_id=run_id,
            budget_config=body.budget_override,
            auth_context=auth.to_dict(),
            policy_id=body.policy_id,
        )

        graph = executor.build_graph()
        config = {"configurable": {"thread_id": run_id}}
        final_state = graph.invoke(initial_state, config=config)

        finished_at = tracer._now_iso()
        signal = final_state.get("global_signal", "")

        if "REJECT" in signal:
            final_status = "blocked"
        elif "HITL" in signal:
            final_status = "hitl_pending"
        elif "DONE" in signal:
            final_status = "completed"
        else:
            final_status = "completed"

        # Persist state
        store.save_state(run_id, final_state)

        # Build and persist trace (Step A)
        trace_doc = tracer.dump(
            state=final_state,
            task=body.task,
            started_at=started_at,
            finished_at=finished_at,
            final_status=final_status,
        )
        store.save_trace(run_id, trace_doc)

        # Compute decisions summary
        decisions = final_state.get("governance_decisions", [])
        summary = {
            "allow": sum(1 for d in decisions if d.get("allowed")),
            "block": sum(1 for d in decisions if not d.get("allowed") and not d.get("hitl_required")),
            "hitl": sum(1 for d in decisions if d.get("hitl_required")),
        }

        # Collect HITL pending steps
        pending_steps = [
            e for e in final_state.get("trace_events", [])
            if e.get("status") == "hitl_pending"
        ]

        store.set_status(
            run_id, final_status,
            final_answer=final_state.get("results", {}).get("final_answer"),
            total_cost_usd=final_state.get("usage_stats", {}).get("total_cost", 0.0),
            decisions_summary=summary,
            pending_steps=pending_steps,
            finished_at=finished_at,
        )
        logger.info(f"Run {run_id} finished: {final_status}")

    except Exception as exc:
        logger.exception(f"Run {run_id} failed with error: {exc}")
        store.set_status(run_id, "error", error=str(exc))


# --------------------------------------------------------------------------- #
#  GET /runs/{id}                                                              #
# --------------------------------------------------------------------------- #

@router.get("/{run_id}", response_model=RunStatusResponse)
def get_run(
    run_id: str,
    auth: Annotated[AuthContext, Depends(require_auth)],
    store: Annotated[RunStore, Depends(get_run_store)],
) -> RunStatusResponse:
    record = store.get_status(run_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run '{run_id}' not found.")

    return RunStatusResponse(
        run_id=run_id,
        status=record.get("status", "running"),
        final_answer=record.get("final_answer"),
        total_cost_usd=record.get("total_cost_usd", 0.0),
        decisions_summary=record.get("decisions_summary", {}),
        trace_url=f"/runs/{run_id}/trace",
        pending_steps=record.get("pending_steps"),
    )


# --------------------------------------------------------------------------- #
#  GET /runs/{id}/trace                                                        #
# --------------------------------------------------------------------------- #

@router.get("/{run_id}/trace")
def get_trace(
    run_id: str,
    auth: Annotated[AuthContext, Depends(require_auth)],
    store: Annotated[RunStore, Depends(get_run_store)],
) -> Dict[str, Any]:
    """Download the full causal dependency graph JSON for a completed run."""
    trace_doc = store.load_trace(run_id)
    if not trace_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Trace for run '{run_id}' not found. The run may still be in progress.",
        )
    return trace_doc


# --------------------------------------------------------------------------- #
#  POST /runs/{id}/approve (Step C — HITL resume)                             #
# --------------------------------------------------------------------------- #

@router.post("/{run_id}/approve", response_model=ApproveResponse)
def approve_run(
    run_id: str,
    body: ApproveRequest,
    approver: Annotated[AuthContext, Depends(require_approver)],
    store: Annotated[RunStore, Depends(get_run_store)],
    executor: Annotated[RecursiveExecutor, Depends(get_executor)],
) -> ApproveResponse:
    """Approve or reject a HITL_PENDING step and resume the graph.

    Requires the 'approver' role in the caller's JWT.
    Returns 409 if the run is not in hitl_pending status.
    """
    record = store.get_status(run_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run '{run_id}' not found.")

    if record.get("status") != "hitl_pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Run '{run_id}' is not in hitl_pending status (current: {record.get('status')}).",
        )

    if body.decision == "reject":
        store.set_status(run_id, "blocked", rejection_reason=body.notes, rejected_by=approver.subject)
        logger.info(f"Run {run_id} HITL rejected by {approver.subject}")
        return ApproveResponse(
            run_id=run_id,
            event_id=body.event_id,
            status="rejected",
            approved_by=approver.subject,
        )

    # Resume via LangGraph checkpointer (requires checkpointer to be configured)
    try:
        from langgraph.types import Command
        graph = executor.build_graph()
        config = {"configurable": {"thread_id": run_id}}
        approval_payload = {
            "approved_by": approver.subject,
            "token_id": approver.token_id,
            "event_id": body.event_id,
            "notes": body.notes,
        }
        final_state = graph.invoke(None, config=config, command=Command(resume=approval_payload))

        finished_at = tracer._now_iso()
        trace_doc = tracer.dump(
            state=final_state,
            started_at=record.get("submitted_at", ""),
            finished_at=finished_at,
            final_status="completed",
        )
        store.save_trace(run_id, trace_doc)
        store.save_state(run_id, final_state)
        store.set_status(
            run_id, "completed",
            final_answer=final_state.get("results", {}).get("final_answer"),
            total_cost_usd=final_state.get("usage_stats", {}).get("total_cost", 0.0),
            finished_at=finished_at,
        )
        logger.info(f"Run {run_id} HITL approved and completed by {approver.subject}")

    except Exception as exc:
        logger.exception(f"Failed to resume run {run_id}: {exc}")
        store.set_status(run_id, "error", error=str(exc))

    return ApproveResponse(
        run_id=run_id,
        event_id=body.event_id,
        status="resumed",
        approved_by=approver.subject,
    )
