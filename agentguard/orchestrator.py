from typing import Dict, Any, List
import logging
from .state import AgentGuardState
from .llm import get_llm, normalize_llm_output

logger = logging.getLogger(__name__)

class ExecutionPlanner:
    def __init__(self, registry):
        self.registry = registry
        self.llm = get_llm()
        if self.llm:
            logger.info(f"ExecutionPlanner initialized with LLM router: {self.llm.__class__.__name__}")
        else:
            logger.info("ExecutionPlanner running in MOCK routing mode (no LLM configured).")

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

        # Get registered agents for routing options
        available_tools = "\n".join([f"- {a['role']}: {a['semantic_description']}" for a in self.registry.list_all()])

        edges = []
        for task in subtasks:
            intent = "analyze" # Default intent

            # Advanced Semantic Intent Extraction (Phase 2 Routing)
            if self.llm:
                prompt = f"Given the task: '{task}', determine the primary intent verb out of the available tools:\n{available_tools}\nRespond with only a single word (e.g., 'search', 'analyze', 'code')."
                try:
                    result = self.llm.invoke(prompt)
                    content = normalize_llm_output(result.content)
                    intent = content.strip().lower()
                except Exception as e:
                    logger.error(f"LLM Intent Extraction failed: {e}. Falling back to default intent.")
            else:
                # Mock Intent Extraction
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
