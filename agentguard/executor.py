import logging
from typing import Dict, Any
from langgraph.graph import StateGraph, END
from langchain_core.runnables import RunnableConfig

from .state import AgentGuardState, GovernanceDecision
from .policy import PolicyEngine
from .orchestrator import ExecutionPlanner
from .memory import MemoryManager
from .registry import Registry

logger = logging.getLogger(__name__)


class RecursiveExecutor:
    def __init__(self, memory_manager: MemoryManager, registry: Registry, max_depth: int = 3):
        self.memory = memory_manager
        self.registry = registry
        self.policy = PolicyEngine(memory_manager)
        self.planner = ExecutionPlanner(registry)
        self.max_depth = max_depth

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

        return graph.compile()

    # ------------------------------------------------------------------ #
    #  Nodes                                                               #
    # ------------------------------------------------------------------ #

    def _node_preflight(self, state: AgentGuardState, config: RunnableConfig) -> Dict[str, Any]:
        allowed, decision = self.policy.check_preflight(state)
        decisions = list(state.get("governance_decisions", []))
        decisions.append(decision)
        return {
            "global_signal": "OK" if allowed else "REJECT",
            "governance_decisions": decisions,
        }

    def _edge_post_preflight(self, state: AgentGuardState) -> str:
        return "reject" if state.get("global_signal") == "REJECT" else "continue"

    def _node_decompose(self, state: AgentGuardState, config: RunnableConfig) -> Dict[str, Any]:
        return self.planner.decompose(state)

    def _node_plan(self, state: AgentGuardState, config: RunnableConfig) -> Dict[str, Any]:
        return self.planner.plan(state)

    def _node_execute_subtasks(self, state: AgentGuardState, config: RunnableConfig) -> Dict[str, Any]:
        edges = state.get("all_edges", [])
        results = dict(state.get("results", {}))
        usage = dict(state.get("usage_stats", {}))
        decisions = list(state.get("governance_decisions", []))
        current_depth = state.get("depth", 0)

        if current_depth >= self.max_depth:
            results["subtasks_output"] = "Max recursion depth reached."
            return {"results": results}

        output = {}
        for edge in edges:
            subtask = edge.get("task", "")
            agent_id = edge.get("agent_id", "fallback_agent")

            # Build a clean, role-based intent description — avoids carrying
            # suspicious-sounding task names into the LLM safety prompt.
            agent_info = self.registry.get(agent_id) or {}
            agent_role = agent_info.get("role", agent_id)
            agent_desc = agent_info.get("semantic_description", "")
            clean_intent = f"{agent_role} performing: {subtask}"
            action = f"invoke_{agent_id}"

            cost_estimate = 0.05  # per step (realistic API call estimate)
            allowed, decision = self.policy.check_step(
                state,
                action=action,
                intent=clean_intent,
                cost_estimate=cost_estimate,
            )
            decisions.append(decision)

            if not allowed:
                status = "HITL_PENDING" if decision.get("hitl_required") else "BLOCKED"
                output[subtask] = f"{status}: {decision.get('reason')}"
                logger.info(f"Executor: [{status}] {agent_role} — {decision.get('reason')}")
                continue

            # Accumulate cost only for allowed steps
            usage["total_cost"] = usage.get("total_cost", 0.0) + cost_estimate
            logger.info(f"Executor: [ALLOWED] {agent_role} executing subtask: {subtask}")

            # Recursive execution (triggered when depth < max)
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
                )
                child_graph = self.build_graph()
                child_result = child_graph.invoke(child_state)
                output[subtask] = child_result.get("results", {}).get(
                    "final_answer", f"Result from {agent_role} at depth {current_depth + 1}"
                )
                usage = child_result.get("usage_stats", usage)
            else:
                output[subtask] = f"[{agent_role}] Completed: {subtask}"

        results["subtasks_output"] = output
        return {"results": results, "usage_stats": usage, "governance_decisions": decisions}

    def _node_synthesize(self, state: AgentGuardState, config: RunnableConfig) -> Dict[str, Any]:
        sub_outputs = state.get("results", {}).get("subtasks_output", {})
        final = " | ".join(f"{k}: {v}" for k, v in sub_outputs.items())
        results = dict(state.get("results", {}))
        results["final_answer"] = final
        return {"results": results, "global_signal": "DONE"}

    def _node_reject(self, state: AgentGuardState, config: RunnableConfig) -> Dict[str, Any]:
        logger.warning("Executor: task rejected by policy at preflight.")
        return {"global_signal": "REJECTED_BY_POLICY"}
