"""
tests/test_eval.py
-------------------
Verifies that:
  - EvalScenario / EvalResult data classes work correctly
  - inline_policy injection overrides the global policy engine cache
  - _NoOpPolicyEngine always allows
  - EvalHarness.run() returns one EvalResult per scenario
  - content_rule_block scenario is self-contained (no policy.yaml dependency)
"""
import pytest

from agentguard.eval import (
    EvalScenario, EvalRunResult, EvalResult, EvalHarness,
    BUILT_IN_SCENARIOS, _NoOpPolicyEngine,
)
from agentguard.memory import MemoryManager
from agentguard.registry import Registry
from agentguard.state import make_initial_state


# --------------------------------------------------------------------------- #
#  Data class tests                                                            #
# --------------------------------------------------------------------------- #

class TestEvalResult:
    def _make(self, with_status="completed", without_status="completed",
               expected="completed", with_cost=0.1, without_cost=0.1):
        scenario = EvalScenario(name="t", task="t", expected_status=expected)
        wp = EvalRunResult(with_status, None, with_cost, 1, 0, 0)
        wop = EvalRunResult(without_status, None, without_cost, 1, 0, 0)
        return EvalResult(scenario=scenario, with_policy=wp, without_policy=wop)

    def test_passed_when_status_matches_expected(self):
        r = self._make(with_status="completed", expected="completed")
        assert r.passed is True

    def test_failed_when_status_mismatches(self):
        r = self._make(with_status="blocked", expected="completed")
        assert r.passed is False

    def test_policy_changed_behavior_true(self):
        r = self._make(with_status="blocked", without_status="completed")
        assert r.policy_changed_behavior is True

    def test_policy_changed_behavior_false(self):
        r = self._make(with_status="completed", without_status="completed")
        assert r.policy_changed_behavior is False

    def test_policy_changed_cost(self):
        r = self._make(with_cost=0.0, without_cost=0.5)
        assert r.policy_changed_cost is True

    def test_policy_unchanged_cost(self):
        r = self._make(with_cost=0.1, without_cost=0.1)
        assert r.policy_changed_cost is False


# --------------------------------------------------------------------------- #
#  NoOpPolicyEngine                                                            #
# --------------------------------------------------------------------------- #

class TestNoOpPolicyEngine:
    def test_check_preflight_always_allows(self):
        engine = _NoOpPolicyEngine()
        state = make_initial_state(task="test")
        allowed, decision = engine.check_preflight(state)
        assert allowed is True
        assert decision["reason"] == "eval:no-policy"

    def test_check_step_always_allows(self):
        engine = _NoOpPolicyEngine()
        state = make_initial_state(task="test")
        allowed, decision = engine.check_step(
            state, action="invoke_agent", intent="do something", cost_estimate=0.05
        )
        assert allowed is True
        assert decision["cost"] == 0.05


# --------------------------------------------------------------------------- #
#  inline_policy injection                                                     #
# --------------------------------------------------------------------------- #

class TestInlinePolicyInjection:
    def test_inline_policy_populates_cache(self):
        """After injection the policy cache contains the scenario's rules."""
        from agentguard.executor import RecursiveExecutor

        inline = {
            "budget": {"max_cost_usd": 5.0, "per_step_limit_usd": 1.0},
            "content_rules": [
                {"id": "r1", "field": "intent", "operator": "contains",
                 "values": ["forbidden"], "action": "block", "reason": "test"},
            ],
        }
        scenario = EvalScenario(
            name="inline_test", task="forbidden action",
            expected_status="blocked", inline_policy=inline,
        )
        memory = MemoryManager()
        registry = Registry()
        executor = RecursiveExecutor(memory_manager=memory, registry=registry, max_depth=1)

        # Simulate what _run_scenario does during with_policy=True
        compiled = executor.policy._compile_rules(inline.get("content_rules", []))
        executor.policy._policies_cache["__global__"] = (inline, compiled)

        policy_dict, compiled_rules = executor.policy._get_policy()
        assert len(compiled_rules) == 1
        assert compiled_rules[0]["id"] == "r1"

    def test_content_rule_block_scenario_self_contained(self):
        """content_rule_block must have an inline_policy — no file dependency."""
        scenario = next(s for s in BUILT_IN_SCENARIOS if s.name == "content_rule_block")
        assert scenario.inline_policy is not None, (
            "content_rule_block must carry inline_policy so it does not depend on policy.yaml"
        )
        rules = scenario.inline_policy.get("content_rules", [])
        assert len(rules) > 0

    def test_inline_policy_blocks_matching_task(self):
        """Inline rules actually block a task containing the forbidden keyword."""
        from agentguard.executor import RecursiveExecutor
        from agentguard.auth import make_auth_context

        inline = {
            "budget": {"max_cost_usd": 5.0, "per_step_limit_usd": 1.0},
            "content_rules": [
                {"id": "block_forbidden", "field": "intent", "operator": "contains",
                 "values": ["forbidden_keyword"], "action": "block", "reason": "eval block"},
            ],
        }
        memory = MemoryManager()
        registry = Registry()
        executor = RecursiveExecutor(memory_manager=memory, registry=registry, max_depth=1)

        compiled = executor.policy._compile_rules(inline.get("content_rules", []))
        executor.policy._policies_cache["__global__"] = (inline, compiled)

        auth = make_auth_context("eval@test")
        state = make_initial_state(
            task="This task uses forbidden_keyword in its description.",
            subject=auth.subject,
            auth_context=auth.to_dict(),
        )
        graph = executor.build_graph()
        result = graph.invoke(state)
        assert "REJECT" in result.get("global_signal", "")


# --------------------------------------------------------------------------- #
#  EvalHarness structure                                                       #
# --------------------------------------------------------------------------- #

class TestEvalHarness:
    def test_run_returns_one_result_per_scenario(self):
        """run() returns exactly as many EvalResult objects as scenarios passed."""
        harness = EvalHarness(
            registry=Registry(), memory_manager=MemoryManager(), max_depth=1
        )
        scenarios = [
            EvalScenario(name="a", task="do something", expected_status="completed"),
            EvalScenario(name="b", task="something else", expected_status="completed"),
        ]
        results = harness.run(scenarios)
        assert len(results) == 2
        assert results[0].scenario.name == "a"
        assert results[1].scenario.name == "b"

    def test_budget_scenario_is_blocked(self):
        """budget_enforcement scenario (sub-penny limit) must always be blocked."""
        harness = EvalHarness(
            registry=Registry(), memory_manager=MemoryManager(), max_depth=1
        )
        scenario = next(s for s in BUILT_IN_SCENARIOS if s.name == "budget_enforcement")
        result = harness._run_scenario(scenario, with_policy=True)
        assert result.status == "blocked"

    def test_budget_scenario_passes_without_policy(self):
        """Same scenario with policy OFF must complete (no budget check)."""
        harness = EvalHarness(
            registry=Registry(), memory_manager=MemoryManager(), max_depth=1
        )
        scenario = next(s for s in BUILT_IN_SCENARIOS if s.name == "budget_enforcement")
        result = harness._run_scenario(scenario, with_policy=False)
        assert result.status == "completed"
