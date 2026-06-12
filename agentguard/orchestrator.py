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

    def decompose(self, state: AgentGuardState) -> Dict[str, Any]:
        """Break the top-level task into focused, clean subtask descriptions.

        Returns a partial state update (LangGraph merges it via the channel
        reducers) — returning the full state would re-append accumulated
        lists like governance_decisions through their ``operator.add`` reducers.
        """
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

        return {"results": {"subtasks": subtasks}}

    def route_intent(self, intent: str) -> List[Dict[str, Any]]:
        """Look up registered agents that can handle this intent."""
        return self.registry.search_by_intent(intent)

    def plan(self, state: AgentGuardState) -> Dict[str, Any]:
        """Map each subtask to the best available agent via intent extraction.

        Returns a partial state update: ``all_edges`` carries the fresh edge
        list for this planning pass (the reducer replaces any prior list,
        which keeps replans from dispatching stale edges twice).

        Optimisation (U-09): when the Registry is backed by PostgreSQL + pgvector,
        the full subtask text is embedded and compared against agent descriptions
        directly — no per-subtask LLM call is needed for intent extraction.
        When PostgreSQL is *not* available the original LLM-based intent
        extraction + substring search is used as a fallback.
        """
        subtasks = state.get("results", {}).get("subtasks", [])
        available_tools = "\n".join(
            f"- {a['role']}: {a['semantic_description']}"
            for a in self.registry.list_all()
        )

        # Check if vector search is active on the registry
        vector_search_active = getattr(self.registry, "_db", None) is not None

        edges = []
        for subtask in subtasks:
            if vector_search_active:
                # Fast path: embed the full subtask, compare directly against
                # agent embeddings — one vector DB query, zero LLM calls.
                agents = self.registry.search_by_intent(subtask)
                logger.debug(
                    f"ExecutionPlanner: vector-routed '{subtask[:60]}' → "
                    f"{agents[0]['id'] if agents else 'fallback'}"
                )
            else:
                # Fallback path: LLM intent extraction + substring search.
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

        return {"all_edges": edges}
