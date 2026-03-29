from typing import Dict, Any, Tuple
import logging
from .state import AgentGuardState, GovernanceDecision
from .llm import get_llm

logger = logging.getLogger(__name__)

class PolicyEngine:
    def __init__(self, memory_manager):
        self.memory = memory_manager
        self.llm = get_llm()
        if self.llm:
            logger.info(f"PolicyEngine initialized with LLM: {self.llm.__class__.__name__}")
        else:
            logger.info("PolicyEngine running in MOCK mode (no LLM configured).")

    def check_preflight(self, state: AgentGuardState) -> Tuple[bool, GovernanceDecision]:
        """Check if the whole task is allowed before starting."""
        task = state.get("task", "").lower()
        if "forbidden" in task or "hack" in task:
            decision = GovernanceDecision(
                action="start_task",
                allowed=False,
                reason="Task contains forbidden keywords.",
                cost=0.0
            )
            return False, decision

        return True, GovernanceDecision(action="start_task", allowed=True, reason="Passes preflight.", cost=0.01)

    def check_step(self, state: AgentGuardState, action: str, intent: str, cost_estimate: float) -> Tuple[bool, GovernanceDecision]:
        """Check if an individual step (tool call or recursion) is allowed."""

        # 0. Inline Semantic Policy Check (LLM-based)
        if self.llm:
            prompt = f"Analyze if this action and intent violates basic safety rules (e.g. no hacking, no PII exposure, no harm). Respond with only 'ALLOW' or 'BLOCK'.\nAction: {action}\nIntent: {intent}"
            try:
                result = self.llm.invoke(prompt)
                content = result.content.strip().upper()
                if "BLOCK" in content:
                    return False, GovernanceDecision(
                         action=action,
                         allowed=False,
                         reason=f"LLM Policy Blocked: Semantic safety violation detected.",
                         cost=0.0
                     )
            except Exception as e:
                logger.error(f"LLM Policy check failed: {e}. Defaulting to BLOCK for safety.")
                return False, GovernanceDecision(action=action, allowed=False, reason="Safety check service unavailable.", cost=0.0)

        # 1. Budget check
        current_cost = state.get("usage_stats", {}).get("total_cost", 0.0)
        budget = state.get("budget_config", {}).get("max_cost", 10.0)

        if current_cost + cost_estimate > budget:
            return False, GovernanceDecision(
                action=action,
                allowed=False,
                reason=f"Budget exceeded. Cost: {current_cost + cost_estimate} > {budget}",
                cost=0.0
            )

        # 2. PII / Keyword check
        if "PII" in action or "ssn" in action.lower():
             return False, GovernanceDecision(
                 action=action,
                 allowed=False,
                 reason="PII detected in action.",
                 cost=0.0
             )

        # 3. Loop detection
        thought = {"action": action, "intent": intent}
        if self.memory.detect_loop(state.get("root_task_id", "default"), thought):
            return False, GovernanceDecision(
                action=action,
                allowed=False,
                reason="Semantic loop detected.",
                cost=0.0
            )

        # Record thought for future loop detection
        self.memory.record_thought(state.get("root_task_id", "default"), thought)

        return True, GovernanceDecision(action=action, allowed=True, reason="Step allowed.", cost=cost_estimate)
