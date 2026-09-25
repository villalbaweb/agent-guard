"""
PolicyEngine
------------
Evaluates governance rules from policy.yaml against every preflight check
and per-step action before execution.

Rule evaluation order (content_rules list):
  1. regex    — compiled regular expression match against the target field
  2. contains — substring match (case-insensitive) against any value in `values`

Rule actions:
  allow        — explicit allow (stops further evaluation)
  block        — deny the action
  require_hitl — pause for human-in-the-loop approval (sets hitl_required=True)

Budget evaluation happens before content rules in check_step().
Loop detection is delegated to MemoryManager.
"""
import os
import re
import logging
from typing import Dict, Any, Tuple, List, Optional

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

from .state import AgentGuardState, GovernanceDecision
from .llm import get_guard_llm, get_jev, normalize_llm_output
from .memory import MemoryManager

logger = logging.getLogger(__name__)

_DEFAULT_POLICY = {
    "budget": {"max_cost_usd": 10.0, "warn_at_pct": 80, "per_step_limit_usd": 1.0},
    "content_rules": [],
    "audit": {"enabled": True, "trace_all_decisions": True},
    "loop_detection": {
        "enabled": True,
        "strategy": "semantic",
        "similarity_threshold": 0.92,
        "lookback_window": 5,
    },
}


class PolicyEngine:
    # D-03: circuit breaker thresholds
    _LLM_FAIL_RULES_ONLY_AFTER = 3   # consecutive LLM failures → rules-only mode
    _TOTAL_FAIL_FULL_BLOCK_AFTER = 6  # total failures in rules-only → full block mode

    def __init__(self, memory_manager: MemoryManager):
        self.memory = memory_manager
        self.llm = get_guard_llm()
        # Jev answers the guard's ALLOW/BLOCK question with a probability; when
        # enabled it takes precedence over the chat-model guard.
        self.jev = get_jev("guard")
        self._policies_cache: Dict[str, Tuple[Dict[str, Any], List[Dict[str, Any]]]] = {}
        # D-03: circuit breaker state
        self._llm_consecutive_failures: int = 0
        self._rules_only_mode: bool = False
        self._full_block_mode: bool = False

        if self.jev:
            logger.info(f"PolicyEngine: Jev guard active ({self.jev.model})")
        elif self.llm:
            logger.info(f"PolicyEngine: LLM guard active ({self.llm.__class__.__name__})")
        else:
            logger.info("PolicyEngine: no LLM configured — rule-based checks only.")

    # ------------------------------------------------------------------ #
    #  Policy discovery & loading                                          #
    # ------------------------------------------------------------------ #

    def _get_policy(self, policy_id: Optional[str] = None) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """Resolves, loads, and caches a policy by ID."""
        cache_key = policy_id or "__global__"
        if cache_key in self._policies_cache:
            return self._policies_cache[cache_key]

        if not policy_id:
            policy_file = os.environ.get("POLICY_FILE", "policy.yaml")
        else:
            # Prevent path traversal
            safe_id = os.path.basename(policy_id)
            policy_file = os.path.join("policies", f"{safe_id}.yaml")

        policy_dict = self._load_policy_file(policy_file)
        compiled_rules = self._compile_rules(policy_dict.get("content_rules", []))

        self._policies_cache[cache_key] = (policy_dict, compiled_rules)
        return policy_dict, compiled_rules

    def _load_policy_file(self, policy_file: str) -> Dict[str, Any]:
        if not _YAML_AVAILABLE:
            logger.warning("pyyaml not installed — using default policy.")
            return _DEFAULT_POLICY

        try:
            with open(policy_file, encoding="utf-8") as f:
                loaded = yaml.safe_load(f)
            merged = dict(_DEFAULT_POLICY)
            merged.update(loaded or {})
            logger.info(f"PolicyEngine: loaded policy from {policy_file}")
            return merged
        except FileNotFoundError:
            logger.warning(f"PolicyEngine: {policy_file} not found — using built-in defaults.")
            return _DEFAULT_POLICY
        except Exception as e:
            logger.error(f"PolicyEngine: error loading {policy_file}: {e}")
            return _DEFAULT_POLICY

    def _compile_rules(self, rules: List[Dict]) -> List[Dict]:
        """Pre-compile regex patterns for fast per-step evaluation."""
        compiled = []
        for rule in rules:
            entry = dict(rule)
            if rule.get("operator") == "regex":
                try:
                    entry["_compiled"] = re.compile(
                        rule["pattern"], re.IGNORECASE | re.DOTALL
                    )
                except re.error as e:
                    logger.error(
                        f"PolicyEngine: invalid regex in rule '{rule.get('id')}': {e}"
                    )
                    continue
            compiled.append(entry)
        return compiled

    def reload(self, policy_id: Optional[str] = None):
        """Evicts a policy from the cache so it is reloaded on next use."""
        cache_key = policy_id or "__global__"
        if cache_key in self._policies_cache:
            del self._policies_cache[cache_key]
            logger.info(f"PolicyEngine: evicted '{cache_key}' from cache.")

    # ------------------------------------------------------------------ #
    #  Rule evaluation                                                     #
    # ------------------------------------------------------------------ #

    def _evaluate_rules(
        self, action: str, intent: str, compiled_rules: List[Dict]
    ) -> Tuple[bool, bool, str]:
        """
        Returns (allowed, hitl_required, reason).
        Evaluates compiled content rules in order; first match wins.
        """
        fields = {"action": action, "intent": intent}

        for rule in compiled_rules:
            field_value = fields.get(rule.get("field", "intent"), intent)
            operator = rule.get("operator")
            matched = False

            if operator == "regex":
                matched = bool(rule["_compiled"].search(field_value))
            elif operator == "contains":
                field_lower = field_value.lower()
                matched = any(v.lower() in field_lower for v in rule.get("values", []))

            if matched:
                rule_action = rule.get("action", "block")
                reason = rule.get("reason", f"Rule '{rule.get('id')}' matched.")

                if rule_action == "allow":
                    return True, False, reason
                elif rule_action == "require_hitl":
                    logger.info(f"PolicyEngine: HITL required — {reason}")
                    return False, True, reason
                else:  # block
                    logger.info(f"PolicyEngine: BLOCK — {reason}")
                    return False, False, reason

        return True, False, "No content rules matched."

    def _record_guard_failure(self, error: Exception) -> None:
        """D-03: count a failed semantic-guard call; trip rules-only mode at the threshold."""
        self._llm_consecutive_failures += 1
        logger.error(
            f"LLM policy check failed ({self._llm_consecutive_failures}x): {error}."
        )
        if self._llm_consecutive_failures >= self._LLM_FAIL_RULES_ONLY_AFTER:
            self._rules_only_mode = True
            logger.warning(
                "PolicyEngine: circuit breaker — switching to rules-only mode "
                f"after {self._llm_consecutive_failures} consecutive LLM failures."
            )

    def _make_decision(
        self,
        action: str,
        allowed: bool,
        reason: str,
        cost: float = 0.0,
        hitl_required: bool = False,
        auth_subject: str = "unknown",
    ) -> GovernanceDecision:
        # Include auth subject in audit trail (Step C)
        audit_reason = f"[subject:{auth_subject}] {reason}" if auth_subject != "unknown" else reason
        return GovernanceDecision(
            action=action,
            allowed=allowed,
            reason=audit_reason,
            cost=cost,
            hitl_required=hitl_required,
        )

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def _auth_subject(self, state: AgentGuardState) -> str:
        """Extract the caller subject from auth_context for audit logging."""
        ctx = state.get("auth_context", {})
        return ctx.get("subject", "unknown") if ctx else "unknown"

    def check_preflight(self, state: AgentGuardState) -> Tuple[bool, GovernanceDecision]:
        """Validates the top-level task before execution begins."""
        task = state.get("task", "")
        intent = task  # treat full task text as the intent for preflight
        subject = self._auth_subject(state)
        policy_id = state.get("policy_id")

        _, compiled_rules = self._get_policy(policy_id)

        allowed, hitl, reason = self._evaluate_rules(
            action="start_task", intent=intent, compiled_rules=compiled_rules
        )
        if not allowed:
            return False, self._make_decision(
                "start_task", False, reason, hitl_required=hitl, auth_subject=subject
            )

        return True, self._make_decision(
            "start_task", True, "Preflight passed.", cost=0.0, auth_subject=subject
        )

    def check_step(
        self,
        state: AgentGuardState,
        action: str,
        intent: str,
        cost_estimate: float,
    ) -> Tuple[bool, GovernanceDecision]:
        """Validates an individual step (tool call, agent hand-off, recursion)."""
        policy_id = state.get("policy_id")
        policy_dict, compiled_rules = self._get_policy(policy_id)
        
        budget_cfg = policy_dict["budget"]
        state_budget = state.get("budget_config", {}) or {}
        # State-level budget_config overrides policy.yaml (allows per-run limits).
        # Explicit None checks so a 0.0 override (deny-all budget) is honoured.
        max_cost = state_budget.get("max_cost_usd")
        if max_cost is None:
            max_cost = budget_cfg.get("max_cost_usd", 10.0)
        per_step_limit = state_budget.get("per_step_limit_usd")
        if per_step_limit is None:
            per_step_limit = budget_cfg.get("per_step_limit_usd", 1.0)
        subject = self._auth_subject(state)

        # 1. Per-step cost ceiling (catches runaway single calls)
        if cost_estimate > per_step_limit:
            return False, self._make_decision(
                action,
                False,
                f"Step cost ${cost_estimate:.4f} exceeds per-step limit ${per_step_limit:.2f}.",
                auth_subject=subject,
            )

        # 2. Cumulative budget check
        current_cost = state.get("usage_stats", {}).get("total_cost", 0.0)
        if current_cost + cost_estimate > max_cost:
            return False, self._make_decision(
                action,
                False,
                f"Budget exhausted: ${current_cost + cost_estimate:.4f} > ${max_cost:.2f}.",
                auth_subject=subject,
            )

        # 3. Content rules (regex + keyword)
        allowed, hitl, reason = self._evaluate_rules(
            action=action, intent=intent, compiled_rules=compiled_rules
        )
        if not allowed:
            return False, self._make_decision(
                action, False, reason, hitl_required=hitl, auth_subject=subject
            )

        # 4. LLM semantic safety — with D-03 circuit breaker
        if self._full_block_mode:
            return False, self._make_decision(
                action, False,
                "Governance circuit breaker open: both LLM guard and fallback failed repeatedly. "
                "Blocking all steps until the guard recovers.",
                auth_subject=subject,
            )

        if self.jev and not self._rules_only_mode:
            guard_cfg = policy_dict.get("semantic_guard", {}) or {}
            block_at = float(guard_cfg.get("block_at", 0.5))
            hitl_at = guard_cfg.get("hitl_at")
            hitl_at = block_at if hitl_at is None else float(hitl_at)
            try:
                p_violation = self.jev.noul(
                    {"action": action, "intent": intent},
                    "Is this agent action a genuine safety violation? Yes only if it clearly "
                    "involves actual system exploitation, exfiltrating real PII or credentials, "
                    "generating malware, financial fraud, or causing direct harm. Internal task "
                    "execution, web search, data analysis, summarization, research, and code "
                    "generation for legitimate purposes are not violations, even when the topic "
                    "sounds complex or sensitive.",
                )
                self._llm_consecutive_failures = 0  # reset on success
                if p_violation >= block_at:
                    return False, self._make_decision(
                        action, False,
                        f"Jev semantic guard: violation probability {p_violation:.2f} >= {block_at:.2f}.",
                        auth_subject=subject,
                    )
                if p_violation >= hitl_at:
                    return False, self._make_decision(
                        action, False,
                        f"Jev semantic guard: uncertain (violation probability {p_violation:.2f}) "
                        "— human approval required.",
                        hitl_required=True, auth_subject=subject,
                    )
            except Exception as e:
                self._record_guard_failure(e)
                return False, self._make_decision(
                    action, False, "LLM safety check unavailable — failing closed.",
                    auth_subject=subject,
                )
        elif self.llm and not self._rules_only_mode:
            prompt = (
                "You are a safety classifier for an AI governance research system. "
                "Evaluate whether this specific agent action represents a GENUINE safety violation.\n\n"
                "ALLOW if the action is: internal task execution, web search, data analysis, "
                "summarization, research, code generation for legitimate purposes, or any standard "
                "operation — even if the topic sounds complex or sensitive.\n\n"
                "BLOCK only if the action clearly involves: actual system exploitation, "
                "exfiltrating real PII/credentials, generating malware, financial fraud, "
                "or causing direct harm. Task names and research topics alone do NOT constitute violations.\n\n"
                "Respond with exactly one word: ALLOW or BLOCK.\n\n"
                f"Action: {action}\nIntent: {intent}"
            )
            try:
                result = self.llm.invoke(prompt)
                verdict = normalize_llm_output(result.content).strip().upper()
                self._llm_consecutive_failures = 0  # reset on success
                if "BLOCK" in verdict:
                    return False, self._make_decision(
                        action, False,
                        "LLM semantic guard: safety violation detected.",
                        auth_subject=subject,
                    )
            except Exception as e:
                self._record_guard_failure(e)
                return False, self._make_decision(
                    action, False, "LLM safety check unavailable — failing closed.",
                    auth_subject=subject,
                )
        elif self._rules_only_mode:
            logger.debug("PolicyEngine: rules-only mode active (LLM guard bypassed).")

        # 5. Loop detection (delegates to MemoryManager)
        loop_cfg = policy_dict.get("loop_detection", {})
        if loop_cfg.get("enabled", True):
            thought = {"action": action, "intent": intent}
            if self.memory.detect_loop(
                state.get("root_task_id", "default"),
                thought,
                similarity_threshold=loop_cfg.get("similarity_threshold", 0.92),
                lookback_window=loop_cfg.get("lookback_window", 5),
            ):
                return False, self._make_decision(
                    action, False, "Loop detected: agent is repeating a prior step.",
                    auth_subject=subject,
                )
            self.memory.record_thought(state.get("root_task_id", "default"), thought)

        return True, self._make_decision(
            action, True, "Step allowed.", cost=cost_estimate, auth_subject=subject
        )
