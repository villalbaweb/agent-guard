import logging
from typing import Dict, Any, TypedDict, Literal
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

        # Nodes
        graph.add_node("preflight", self._node_preflight)
        graph.add_node("decompose", self._node_decompose)
        graph.add_node("plan", self._node_plan)
        graph.add_node("execute_subtasks", self._node_execute_subtasks)
        graph.add_node("synthesize", self._node_synthesize)
        graph.add_node("reject", self._node_reject)

        # Edges
        graph.set_entry_point("preflight")
        graph.add_conditional_edges(
            "preflight",
            self._edge_post_preflight,
            {"continue": "decompose", "reject": "reject"}
        )
        graph.add_edge("decompose", "plan")
        graph.add_edge("plan", "execute_subtasks")
        graph.add_edge("execute_subtasks", "synthesize")
        graph.add_edge("synthesize", END)
        graph.add_edge("reject", END)

        return graph.compile()

    def _node_preflight(self, state: AgentGuardState, config: RunnableConfig) -> Dict[str, Any]:
        """Check if the initial request is valid according to global policies."""
        allowed, decision = self.policy.check_preflight(state)
        decisions = state.get("governance_decisions", [])
        decisions.append(decision)
        return {
            "global_signal": "OK" if allowed else "REJECT",
            "governance_decisions": decisions
        }

    def _edge_post_preflight(self, state: AgentGuardState) -> str:
        if state.get("global_signal") == "REJECT":
            return "reject"
        return "continue"

    def _node_decompose(self, state: AgentGuardState, config: RunnableConfig) -> Dict[str, Any]:
        return self.planner.decompose(state)

    def _node_plan(self, state: AgentGuardState, config: RunnableConfig) -> Dict[str, Any]:
        return self.planner.plan(state)

    def _node_execute_subtasks(self, state: AgentGuardState, config: RunnableConfig) -> Dict[str, Any]:
        """Execute the planned subtasks recursively or via simple tool calls."""
        edges = state.get("all_edges", [])
        results = state.get("results", {})
        usage = state.get("usage_stats", {})

        current_depth = state.get("depth", 0)

        if current_depth >= self.max_depth:
            results["subtasks_output"] = "Max depth reached. Stopping recursion."
            return {"results": results}

        output = {}
        for edge in edges:
            task = edge.get("task")
            agent_id = edge.get("agent_id")

            # 1. Step check (inline policy)
            cost_estimate = 0.5 # Mock cost per tool/agent call
            allowed, decision = self.policy.check_step(state, action=f"execute_{agent_id}", intent=task, cost_estimate=cost_estimate)

            if not allowed:
                output[task] = f"BLOCKED: {decision.get('reason')}"
                continue

            # 2. Update usage stats
            usage["total_cost"] = usage.get("total_cost", 0.0) + cost_estimate

            # 3. Recursive execution (mocked by a simple result if not deep, or recursive call if deep)
            if "fork bomb" in state.get("task", "").lower() and current_depth < self.max_depth:
                # Simulate a recursive call by building a child state
                child_state = {
                    "task": task,
                    "depth": current_depth + 1,
                    "root_task_id": state.get("root_task_id"),
                    "budget_config": state.get("budget_config"),
                    "usage_stats": usage, # Share budget
                    "results": {},
                    "all_edges": []
                }
                # To simulate a true fork bomb, we run the subgraph again.
                child_graph = self.build_graph()
                child_result = child_graph.invoke(child_state)
                output[task] = child_result.get("results", {}).get("final_answer", f"Recursive result at depth {current_depth + 1}")
                # Update usage
                usage = child_result.get("usage_stats", usage)
            else:
                 output[task] = f"Result of {task} by {agent_id}"

        results["subtasks_output"] = output
        return {"results": results, "usage_stats": usage}

    def _node_synthesize(self, state: AgentGuardState, config: RunnableConfig) -> Dict[str, Any]:
        results = state.get("results", {})
        sub_outputs = results.get("subtasks_output", {})
        results["final_answer"] = f"Synthesized answer from: {sub_outputs}"
        return {"results": results, "global_signal": "DONE"}

    def _node_reject(self, state: AgentGuardState, config: RunnableConfig) -> Dict[str, Any]:
        return {"global_signal": "REJECTED_BY_POLICY"}
