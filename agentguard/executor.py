"""
executor.py — RecursiveExecutor
---------------------------------
LangGraph-based state machine that orchestrates multi-step agentic tasks under
full policy governance.

Step A additions: emits TraceEvent entries at every node boundary and after each
check_step call.  Events are append-only in state.trace_events.

Step C additions: propagates auth_context into child graphs and emits
LangGraph interrupt() when a require_hitl rule fires.  Expired auth_context
blocks the step before the policy engine is called.
"""
import logging
import time
from typing import Dict, Any, Optional

from langgraph.graph import StateGraph, END
from langchain_core.runnables import RunnableConfig

from .state import AgentGuardState, GovernanceDecision
from .policy import PolicyEngine
from .orchestrator import ExecutionPlanner
from .memory import MemoryManager
from .registry import Registry
from . import trace

logger = logging.getLogger(__name__)


class RecursiveExecutor:
    def __init__(
        self,
        memory_manager: MemoryManager,
        registry: Registry,
        max_depth: int = 3,
        checkpointer=None,
    ):
        self.memory = memory_manager
        self.registry = registry
        self.policy = PolicyEngine(memory_manager)
        self.planner = ExecutionPlanner(registry)
        self.max_depth = max_depth
        self.checkpointer = checkpointer  # optional langgraph checkpointer for HITL

    def build_graph(self):
        graph = StateGraph(AgentGuardState)

        graph.add_node("preflight", self._node_preflight)
        graph.add_node("decompose", self._node_decompose)
        graph.add_node("plan", self._node_plan)
        graph.add_node("execute_subtasks", self._node_execute_subtasks)
        graph.add_node("synthesize", self._node_synthesize)
        graph.add_node("reject", self._node_reject)

        graph.set_entry_point("preflight")
        graph.add_conditional_edges(
            "preflight",
            self._edge_post_preflight,
            {"continue": "decompose", "reject": "reject"},
        )
        graph.add_edge("decompose", "plan")
        graph.add_edge("plan", "execute_subtasks")
        graph.add_edge("execute_subtasks", "synthesize")
        graph.add_edge("synthesize", END)
        graph.add_edge("reject", END)

        compile_kwargs: Dict[str, Any] = {}
        if self.checkpointer is not None:
            compile_kwargs["checkpointer"] = self.checkpointer

        return graph.compile(**compile_kwargs)

    # ------------------------------------------------------------------ #
    #  Auth helpers                                                        #
    # ------------------------------------------------------------------ #

    def _get_auth_context(self, state: AgentGuardState):
        """Return the auth.AuthContext object from state, or None."""
        raw = state.get("auth_context")
        if not raw:
            return None
        try:
            from .auth import AuthContext
            return AuthContext.from_dict(raw)
        except Exception:
            return None

    def _auth_expired(self, state: AgentGuardState) -> bool:
        ctx = self._get_auth_context(state)
        return ctx is not None and ctx.is_expired()

    # ------------------------------------------------------------------ #
    #  Nodes                                                               #
    # ------------------------------------------------------------------ #

    def _node_preflight(self, state: AgentGuardState, config: RunnableConfig) -> Dict[str, Any]:
        t_start = time.monotonic()
        run_id = state.get("root_task_id", "unknown")
        depth = state.get("depth", 0)
        trace_events = list(state.get("trace_events", []))
        parent_event_id = state.get("parent_trace_event_id")

        # Auth expiry check (Step C)
        if self._auth_expired(state):
            ctx = self._get_auth_context(state)
            subject = ctx.subject if ctx else "unknown"
            reason = f"auth_context expired for subject '{subject}'."
            ev = trace.new_event(
                run_id=run_id, node="preflight", depth=depth,
                parent_event_id=parent_event_id,
                action="start_task", status="error",
                metadata={"reason": reason},
            )
            trace_events.append(ev)
            decision = GovernanceDecision(
                action="start_task", allowed=False, reason=reason,
                cost=0.0, hitl_required=False,
            )
            return {
                "global_signal": "REJECT",
                "governance_decisions": list(state.get("governance_decisions", [])) + [decision],
                "trace_events": trace_events,
            }

        allowed, decision = self.policy.check_preflight(state)
        status = "ok" if allowed else "blocked"

        duration_ms = int((time.monotonic() - t_start) * 1000)
        ev = trace.new_event(
            run_id=run_id, node="preflight", depth=depth,
            parent_event_id=parent_event_id,
            action="start_task",
            intent=state.get("task", ""),
            decision=decision,
            status=status,
            duration_ms=duration_ms,
        )
        trace_events.append(ev)

        decisions = list(state.get("governance_decisions", []))
        decisions.append(decision)
        return {
            "global_signal": "OK" if allowed else "REJECT",
            "governance_decisions": decisions,
            "trace_events": trace_events,
        }

    def _edge_post_preflight(self, state: AgentGuardState) -> str:
        return "reject" if state.get("global_signal") == "REJECT" else "continue"

    def _node_decompose(self, state: AgentGuardState, config: RunnableConfig) -> Dict[str, Any]:
        t_start = time.monotonic()
        run_id = state.get("root_task_id", "unknown")
        depth = state.get("depth", 0)
        trace_events = list(state.get("trace_events", []))
        parent_event_id = trace_events[-1]["event_id"] if trace_events else None

        result = self.planner.decompose(state)

        duration_ms = int((time.monotonic() - t_start) * 1000)
        subtasks = [e.get("task", "") for e in result.get("all_edges", [])]
        ev = trace.new_event(
            run_id=run_id, node="decompose", depth=depth,
            parent_event_id=parent_event_id,
            action="decompose_task",
            intent=state.get("task", ""),
            status="ok",
            duration_ms=duration_ms,
            metadata={"subtasks": subtasks},
        )
        trace_events.append(ev)
        result["trace_events"] = trace_events
        return result

    def _node_plan(self, state: AgentGuardState, config: RunnableConfig) -> Dict[str, Any]:
        t_start = time.monotonic()
        run_id = state.get("root_task_id", "unknown")
        depth = state.get("depth", 0)
        trace_events = list(state.get("trace_events", []))
        parent_event_id = trace_events[-1]["event_id"] if trace_events else None

        result = self.planner.plan(state)

        duration_ms = int((time.monotonic() - t_start) * 1000)
        ev = trace.new_event(
            run_id=run_id, node="plan", depth=depth,
            parent_event_id=parent_event_id,
            action="assign_agents",
            status="ok",
            duration_ms=duration_ms,
            metadata={"edges": result.get("all_edges", [])},
        )
        trace_events.append(ev)
        result["trace_events"] = trace_events
        return result

    def _node_execute_subtasks(self, state: AgentGuardState, config: RunnableConfig) -> Dict[str, Any]:
        edges = state.get("all_edges", [])
        results = dict(state.get("results", {}))
        usage = dict(state.get("usage_stats", {}))
        decisions = list(state.get("governance_decisions", []))
        trace_events = list(state.get("trace_events", []))
        current_depth = state.get("depth", 0)
        run_id = state.get("root_task_id", "unknown")

        # Parent event for per-step children is the plan node event
        node_parent_id = trace_events[-1]["event_id"] if trace_events else None

        # Emit the node-level execute_subtasks event
        exec_ev = trace.new_event(
            run_id=run_id, node="execute_subtasks", depth=current_depth,
            parent_event_id=node_parent_id,
            action="execute_subtasks",
            status="ok",
            metadata={"edge_count": len(edges)},
        )
        trace_events.append(exec_ev)
        exec_event_id = exec_ev["event_id"]

        if current_depth >= self.max_depth:
            results["subtasks_output"] = "Max recursion depth reached."
            return {"results": results, "trace_events": trace_events}

        output = {}
        hitl_pending_items = []
        global_signal = state.get("global_signal", "OK")

        for edge in edges:
            subtask = edge.get("task", "")
            agent_id = edge.get("agent_id", "fallback_agent")

            agent_info = self.registry.get(agent_id) or {}
            agent_role = agent_info.get("role", agent_id)
            clean_intent = f"{agent_role} performing: {subtask}"
            action = f"invoke_{agent_id}"

            cost_estimate = 0.05

            # Auth expiry check before every step (Step C)
            if self._auth_expired(state):
                ctx = self._get_auth_context(state)
                subject = ctx.subject if ctx else "unknown"
                reason = f"auth_context expired for subject '{subject}'."
                decision = GovernanceDecision(
                    action=action, allowed=False, reason=reason,
                    cost=0.0, hitl_required=False,
                )
                decisions.append(decision)
                ev = trace.new_event(
                    run_id=run_id, node="execute_subtasks", depth=current_depth,
                    parent_event_id=exec_event_id,
                    agent_id=agent_id, action=action, intent=clean_intent,
                    decision=decision, status="error",
                )
                trace_events.append(ev)
                output[subtask] = f"BLOCKED: {reason}"
                continue

            allowed, decision = self.policy.check_step(
                state, action=action, intent=clean_intent, cost_estimate=cost_estimate,
            )
            decisions.append(decision)

            # Determine auth subject for audit (Step C)
            auth_ctx = self._get_auth_context(state)
            auth_subject = auth_ctx.subject if auth_ctx else state.get("subject", "unknown")

            ev_status = "ok" if allowed else ("hitl_pending" if decision.get("hitl_required") else "blocked")
            ev = trace.new_event(
                run_id=run_id, node="execute_subtasks", depth=current_depth,
                parent_event_id=exec_event_id,
                agent_id=agent_id, action=action, intent=clean_intent,
                decision=decision,
                cost_delta=cost_estimate if allowed else 0.0,
                cumulative_cost=usage.get("total_cost", 0.0) + (cost_estimate if allowed else 0.0),
                status=ev_status,
                metadata={"auth_subject": auth_subject},
            )
            trace_events.append(ev)

            if not allowed:
                if decision.get("hitl_required"):
                    hitl_payload = {
                        "event_id": ev["event_id"],
                        "subtask": subtask,
                        "agent_id": agent_id,
                        "reason": decision.get("reason"),
                        "required_role": "approver",
                        "auth_subject": auth_subject,
                    }
                    hitl_pending_items.append(hitl_payload)
                    output[subtask] = f"HITL_PENDING: {decision.get('reason')}"
                    logger.info(f"Executor: [HITL_PENDING] {agent_role} — {decision.get('reason')}")
                else:
                    output[subtask] = f"BLOCKED: {decision.get('reason')}"
                    logger.info(f"Executor: [BLOCKED] {agent_role} — {decision.get('reason')}")
                continue

            usage["total_cost"] = usage.get("total_cost", 0.0) + cost_estimate
            logger.info(f"Executor: [ALLOWED] {agent_role} executing subtask: {subtask}")

            if current_depth < self.max_depth - 1:
                child_state = AgentGuardState(
                    task=subtask,
                    subject=state.get("subject", ""),
                    root_task_id=state.get("root_task_id", "default"),
                    parent_node_id=agent_id,
                    depth=current_depth + 1,
                    results={},
                    all_agents=[],
                    all_edges=[],
                    global_signal="",
                    usage_stats=usage,
                    budget_config=state.get("budget_config", {}),
                    governance_decisions=[],
                    trace_events=[],
                    auth_context=state.get("auth_context", {}),      # propagate identity (Step C)
                    parent_trace_event_id=exec_event_id,              # causal link (Step A)
                )
                # Child graphs never need a checkpointer — HITL only applies at root level
                child_executor = RecursiveExecutor(
                    memory_manager=self.memory,
                    registry=self.registry,
                    max_depth=self.max_depth,
                    checkpointer=None,
                )
                child_graph = child_executor.build_graph()
                child_result = child_graph.invoke(child_state)

                # Merge child trace events into parent (causal linkage preserved)
                trace_events.extend(child_result.get("trace_events", []))

                output[subtask] = child_result.get("results", {}).get(
                    "final_answer", f"Result from {agent_role} at depth {current_depth + 1}"
                )
                usage = child_result.get("usage_stats", usage)
                decisions.extend(child_result.get("governance_decisions", []))
            else:
                output[subtask] = f"[{agent_role}] Completed: {subtask}"

        # Emit LangGraph interrupt if any HITL items were collected (Step C)
        if hitl_pending_items:
            global_signal = "HITL_PENDING"
            try:
                from langgraph.types import interrupt as lg_interrupt
                approval = lg_interrupt(hitl_pending_items)
                # Code below only executes on resume (after POST /approve)
                approver = "unknown"
                if isinstance(approval, dict):
                    approver = approval.get("approved_by", "unknown")
                logger.info(f"Executor: [HITL_RESUMED] approved by {approver}")
                # Record approval in trace
                approval_ev = trace.new_event(
                    run_id=run_id, node="execute_subtasks", depth=current_depth,
                    parent_event_id=exec_event_id,
                    action="hitl_approved",
                    status="ok",
                    metadata={"approved_by": approver, "items": hitl_pending_items},
                )
                trace_events.append(approval_ev)
                global_signal = "OK"
            except Exception as e:
                # No checkpointer configured — HITL_PENDING signal is set, caller handles it
                logger.warning(
                    f"Executor: HITL interrupt not available ({type(e).__name__}). "
                    "Configure a LangGraph checkpointer to enable pause/resume."
                )

        results["subtasks_output"] = output
        return {
            "results": results,
            "usage_stats": usage,
            "governance_decisions": decisions,
            "trace_events": trace_events,
            "global_signal": global_signal,
        }

    def _node_synthesize(self, state: AgentGuardState, config: RunnableConfig) -> Dict[str, Any]:
        t_start = time.monotonic()
        run_id = state.get("root_task_id", "unknown")
        depth = state.get("depth", 0)
        trace_events = list(state.get("trace_events", []))
        parent_event_id = trace_events[-1]["event_id"] if trace_events else None

        sub_outputs = state.get("results", {}).get("subtasks_output", {})
        final = " | ".join(f"{k}: {v}" for k, v in sub_outputs.items())
        results = dict(state.get("results", {}))
        results["final_answer"] = final

        duration_ms = int((time.monotonic() - t_start) * 1000)
        ev = trace.new_event(
            run_id=run_id, node="synthesize", depth=depth,
            parent_event_id=parent_event_id,
            action="synthesize_results",
            status="ok",
            duration_ms=duration_ms,
            metadata={"answer_length": len(final)},
        )
        trace_events.append(ev)

        return {"results": results, "global_signal": "DONE", "trace_events": trace_events}

    def _node_reject(self, state: AgentGuardState, config: RunnableConfig) -> Dict[str, Any]:
        run_id = state.get("root_task_id", "unknown")
        depth = state.get("depth", 0)
        trace_events = list(state.get("trace_events", []))
        parent_event_id = trace_events[-1]["event_id"] if trace_events else None

        logger.warning("Executor: task rejected by policy at preflight.")
        ev = trace.new_event(
            run_id=run_id, node="reject", depth=depth,
            parent_event_id=parent_event_id,
            action="reject_task",
            status="blocked",
        )
        trace_events.append(ev)
        return {"global_signal": "REJECTED_BY_POLICY", "trace_events": trace_events}
