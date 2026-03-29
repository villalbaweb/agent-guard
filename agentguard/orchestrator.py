from typing import Dict, Any, List
import logging
from .state import AgentGuardState

logger = logging.getLogger(__name__)

class ExecutionPlanner:
    def __init__(self, registry):
        self.registry = registry

    def decompose(self, state: AgentGuardState) -> AgentGuardState:
        """Decompose a high-level task into sub-intents."""
        task = state.get("task", "")
        # Mock decomposition
        subtasks = [f"{task} - part 1", f"{task} - part 2"]
        if state.get("depth", 0) > 1:
            subtasks = [f"{task} - final detail"]

        state["results"] = state.get("results", {})
        state["results"]["subtasks"] = subtasks
        return state

    def route_intent(self, intent: str) -> List[Dict[str, Any]]:
        """Look up agents/tools in the registry that can handle this intent."""
        # Simple semantic search using the registry
        matched_agents = self.registry.search_by_intent(intent)
        return matched_agents

    def plan(self, state: AgentGuardState) -> AgentGuardState:
        """Map subtasks to agents based on intent."""
        subtasks = state.get("results", {}).get("subtasks", [])

        edges = []
        for task in subtasks:
            # Simple keyword extraction for intent
            intent = "search" if "search" in task.lower() else "analyze"

            agents = self.route_intent(intent)
            if agents:
                edges.append({
                    "task": task,
                    "agent_id": agents[0]["id"]
                })
            else:
                 edges.append({
                    "task": task,
                    "agent_id": "fallback_agent"
                 })

        state["all_edges"] = state.get("all_edges", []) + edges
        return state
