import json
import re
import logging
from typing import Dict, Any, List
from .state import AgentGuardState
from .llm import get_llm, normalize_llm_output

logger = logging.getLogger(__name__)


class ExecutionPlanner:
    def __init__(self, registry):
        self.registry = registry
        self.llm = get_llm()
        if self.llm:
            logger.info(f"ExecutionPlanner: LLM router active ({self.llm.__class__.__name__})")
        else:
            logger.info("ExecutionPlanner: no LLM — using mock routing.")

    def decompose(self, state: AgentGuardState) -> AgentGuardState:
        """Break the top-level task into focused, clean subtask descriptions."""
        task = state.get("task", "")
        subtasks = []

        if self.llm:
            prompt = (
                "You are a task planner for an AI agent system. "
                "Break the following task into 2-3 focused, concrete subtasks. "
                "Each subtask should be a short, action-oriented description (under 15 words). "
                "Reply with ONLY a valid JSON array of strings — no explanation, no markdown.\n\n"
                f"Task: {task}\n\n"
                'Example: ["Search for recent GDP data", "Analyze trends across regions", "Summarize key findings"]'
            )
            try:
                result = self.llm.invoke(prompt)
                content = normalize_llm_output(result.content).strip()
                # Strip markdown fences if present
                content = re.sub(r"```(?:json)?|```", "", content).strip()
                match = re.search(r"\[.*\]", content, re.DOTALL)
                if match:
                    parsed = json.loads(match.group())
                    if isinstance(parsed, list) and parsed:
                        subtasks = [str(s).strip() for s in parsed if s]
                        logger.info(f"ExecutionPlanner: decomposed into {len(subtasks)} subtasks")
            except Exception as e:
                logger.error(f"ExecutionPlanner: LLM decomposition failed ({e}) — using fallback.")

        if not subtasks:
            subtasks = [f"Research the topic: {task}", f"Analyze and summarize: {task}"]

        state["results"] = state.get("results", {})
        state["results"]["subtasks"] = subtasks
        return state

    def route_intent(self, intent: str) -> List[Dict[str, Any]]:
        """Look up registered agents that can handle this intent."""
        return self.registry.search_by_intent(intent)

    def plan(self, state: AgentGuardState) -> AgentGuardState:
        """Map each subtask to the best available agent via intent extraction."""
        subtasks = state.get("results", {}).get("subtasks", [])
        available_tools = "\n".join(
            f"- {a['role']}: {a['semantic_description']}"
            for a in self.registry.list_all()
        )

        edges = []
        for subtask in subtasks:
            intent = "analyze"  # safe default

            if self.llm:
                prompt = (
                    f"You are a router. Given this subtask, pick the best matching role "
                    f"from the list below and respond with ONLY the primary action verb "
                    f"(one lowercase word like 'search' or 'analyze').\n\n"
                    f"Subtask: {subtask}\n\n"
                    f"Available roles:\n{available_tools}"
                )
                try:
                    result = self.llm.invoke(prompt)
                    raw = normalize_llm_output(result.content).strip().lower()
                    # Take only the first word to avoid multi-word responses
                    intent = raw.split()[0] if raw else "analyze"
                except Exception as e:
                    logger.error(f"ExecutionPlanner: intent extraction failed ({e}).")
            else:
                intent = "search" if "search" in subtask.lower() else "analyze"

            agents = self.route_intent(intent)
            edges.append({
                "task": subtask,
                "agent_id": agents[0]["id"] if agents else "fallback_agent",
            })

        state["all_edges"] = state.get("all_edges", []) + edges
        return state
