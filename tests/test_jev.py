"""
tests/test_jev.py — Jev (TypeSafe decision model) integration
--------------------------------------------------------------
Verifies:
  - get_jev() is opt-in per use case via AGENTGUARD_JEV and needs OPENROUTER_API_KEY
  - JevClient request shape and answer parsing (httpx MockTransport, no network)
  - PolicyEngine guard: block / HITL / allow thresholds and circuit breaker on failure
  - Reflection: replan decided by P(adequate)
  - Planner routing: one batched choice request, fallback when Jev fails
"""
import json
import os
from unittest.mock import MagicMock, patch

import httpx
import pytest

from agentguard.executor import RecursiveExecutor
from agentguard.jev import JevClient, JevError
from agentguard.llm import get_jev, get_jev_use_cases
from agentguard.memory import MemoryManager
from agentguard.orchestrator import ExecutionPlanner
from agentguard.policy import PolicyEngine
from agentguard.registry import Registry
from agentguard.state import make_initial_state


def _env(**overrides):
    merged = {"AGENTGUARD_JEV": "", "OPENROUTER_API_KEY": "", "AGENTGUARD_JEV_MODEL": ""}
    merged.update(overrides)
    return patch.dict(os.environ, merged, clear=False)


def _client_with(handler) -> JevClient:
    client = JevClient(api_key="sk-or-test")
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    return client


class _FakeJev:
    """Stands in for JevClient at the call sites."""

    def __init__(self, noul=None, choices=None, error=None):
        self._noul, self._choices, self._error = noul, choices, error
        self.calls = []

    def noul(self, state, instructions):
        self.calls.append(("noul", state, instructions))
        if self._error:
            raise self._error
        return self._noul

    def choices(self, state, questions):
        self.calls.append(("choices", state, questions))
        if self._error:
            raise self._error
        return self._choices(questions)


# --------------------------------------------------------------------------- #
#  get_jev()                                                                   #
# --------------------------------------------------------------------------- #

class TestGetJev:
    def test_disabled_by_default(self):
        with _env(OPENROUTER_API_KEY="sk-or-test"):
            assert get_jev("guard") is None
            assert get_jev_use_cases() == []

    def test_enabled_per_use_case(self):
        with _env(AGENTGUARD_JEV="guard,route", OPENROUTER_API_KEY="sk-or-test"):
            assert isinstance(get_jev("guard"), JevClient)
            assert isinstance(get_jev("route"), JevClient)
            assert get_jev("reflect") is None

    def test_all(self):
        with _env(AGENTGUARD_JEV="all", OPENROUTER_API_KEY="sk-or-test"):
            assert get_jev_use_cases() == ["guard", "reflect", "route"]

    def test_default_model_is_pinned(self):
        with _env(AGENTGUARD_JEV="guard", OPENROUTER_API_KEY="sk-or-test"):
            assert get_jev("guard").model == "typesafe/jev-1.13"

    def test_model_override(self):
        with _env(AGENTGUARD_JEV="guard", OPENROUTER_API_KEY="sk-or-test",
                  AGENTGUARD_JEV_MODEL="~typesafe/jev-latest"):
            assert get_jev("guard").model == "~typesafe/jev-latest"

    def test_missing_key_returns_none(self):
        with _env(AGENTGUARD_JEV="all"):
            assert get_jev("guard") is None

    def test_unknown_use_case_raises(self):
        with pytest.raises(ValueError):
            get_jev("decompose")


# --------------------------------------------------------------------------- #
#  JevClient                                                                   #
# --------------------------------------------------------------------------- #

class TestJevClient:
    def test_noul_request_and_parse(self):
        seen = {}

        def handler(request):
            seen.update(json.loads(request.content))
            return httpx.Response(200, json={
                "answers": {"q": {"type": "noul", "noul": 0.04}},
                "usage": {"cost": 1e-05},
            })

        p = _client_with(handler).noul({"action": "a"}, "Is it bad?")
        assert p == pytest.approx(0.04)
        assert seen["model"] == "typesafe/jev-1.13"
        assert seen["state"] == {"action": "a"}
        assert seen["questions"] == {"q": {"type": "noul", "instructions": "Is it bad?"}}

    def test_choices_parse(self):
        def handler(request):
            return httpx.Response(200, json={"answers": {"s0": {
                "type": "choice", "choice": "researcher",
                "probabilities": {"researcher": 1, "writer": 0}, "confidence": 1,
            }}})

        answers = _client_with(handler).choices(
            "task", {"s0": ("Who?", {"researcher": "search", "writer": "prose"})}
        )
        assert answers["s0"]["choice"] == "researcher"

    def test_invalid_choice_raises(self):
        def handler(request):
            return httpx.Response(200, json={"answers": {"s0": {"type": "choice", "choice": "ghost"}}})

        with pytest.raises(JevError):
            _client_with(handler).choices("task", {"s0": ("Who?", {"researcher": "search"})})

    def test_http_error_raises(self):
        def handler(request):
            return httpx.Response(429, json={"error": {"code": 429, "message": "rate limited"}})

        with pytest.raises(JevError, match="429"):
            _client_with(handler).noul("s", "q?")

    def test_missing_answer_raises(self):
        def handler(request):
            return httpx.Response(200, json={"answers": {}})

        with pytest.raises(JevError):
            _client_with(handler).noul("s", "q?")


# --------------------------------------------------------------------------- #
#  PolicyEngine guard                                                          #
# --------------------------------------------------------------------------- #

def _engine(jev, semantic_guard=None):
    pe = PolicyEngine(MemoryManager())
    pe.llm = None
    pe.jev = jev
    policy = {
        "budget": {"max_cost_usd": 10.0, "per_step_limit_usd": 1.0},
        "content_rules": [],
        "loop_detection": {"enabled": False},
    }
    if semantic_guard is not None:
        policy["semantic_guard"] = semantic_guard
    pe._get_policy = lambda policy_id=None: (policy, [])
    return pe


def _step(pe):
    return pe.check_step(make_initial_state(task="t"), action="invoke_agent",
                         intent="summarize data", cost_estimate=0.01)


class TestJevGuard:
    def test_low_probability_allows(self):
        allowed, decision = _step(_engine(_FakeJev(noul=0.04)))
        assert allowed is True

    def test_high_probability_blocks(self):
        allowed, decision = _step(_engine(_FakeJev(noul=0.9)))
        assert allowed is False
        assert decision["hitl_required"] is False
        assert "0.90" in decision["reason"]

    def test_hitl_band(self):
        pe = _engine(_FakeJev(noul=0.4), semantic_guard={"block_at": 0.8, "hitl_at": 0.3})
        allowed, decision = _step(pe)
        assert allowed is False
        assert decision["hitl_required"] is True

    def test_state_carries_action_and_intent(self):
        jev = _FakeJev(noul=0.0)
        _step(_engine(jev))
        assert jev.calls[0][1] == {"action": "invoke_agent", "intent": "summarize data"}

    def test_failure_fails_closed_and_trips_breaker(self):
        pe = _engine(_FakeJev(error=JevError("down")))
        for _ in range(PolicyEngine._LLM_FAIL_RULES_ONLY_AFTER):
            allowed, decision = _step(pe)
            assert allowed is False
            assert "failing closed" in decision["reason"]
        assert pe._rules_only_mode is True
        allowed, _ = _step(pe)  # rules-only: guard bypassed
        assert allowed is True


# --------------------------------------------------------------------------- #
#  Reflection                                                                  #
# --------------------------------------------------------------------------- #

def _reflect(jev):
    executor = RecursiveExecutor(
        memory_manager=MemoryManager(), registry=Registry(), max_depth=1, enable_reflection=True,
    )
    executor.policy.llm = None
    executor.reflect_jev = jev
    state = make_initial_state(task="Summarize GDP trends")
    state["results"] = {"final_answer": "GDP grew 2%."}
    state["reflection_count"] = 0
    return executor._node_reflect(state, config=MagicMock())


class TestJevReflection:
    def test_adequate_does_not_replan(self):
        result = _reflect(_FakeJev(noul=0.9))
        assert result.get("reflection_count", 0) == 0
        assert result["trace_events"][0]["metadata"]["p_adequate"] == pytest.approx(0.9)

    def test_inadequate_replans(self):
        result = _reflect(_FakeJev(noul=0.1))
        assert result["reflection_count"] == 1
        assert result["results"] == {"final_answer": ""}

    def test_failure_skips_replan(self):
        result = _reflect(_FakeJev(error=JevError("down")))
        assert result.get("reflection_count", 0) == 0


# --------------------------------------------------------------------------- #
#  Planner routing                                                             #
# --------------------------------------------------------------------------- #

def _planner(jev):
    registry = Registry()
    registry.register("researcher", "research", "Web search and data gathering", {}, {})
    registry.register("writer", "write", "Drafting prose and summaries", {}, {})
    planner = ExecutionPlanner(registry)
    planner.llm = None
    planner.jev = jev
    return planner


def _plan_state():
    state = make_initial_state(task="GDP report")
    state["results"] = {"subtasks": ["Find GDP data", "Write the summary"]}
    return state


class TestJevRouting:
    def test_routes_all_subtasks_in_one_request(self):
        picks = {"subtask_0": "researcher", "subtask_1": "writer"}
        jev = _FakeJev(choices=lambda qs: {k: {"choice": picks[k], "confidence": 1} for k in qs})
        edges = _planner(jev).plan(_plan_state())["all_edges"]

        assert [e["agent_id"] for e in edges] == ["researcher", "writer"]
        assert len(jev.calls) == 1
        _, _, questions = jev.calls[0]
        assert set(questions["subtask_0"][1]) >= {"researcher", "writer"}

    def test_failure_falls_back(self):
        edges = _planner(_FakeJev(error=JevError("down"))).plan(_plan_state())["all_edges"]
        assert len(edges) == 2
        assert all(e["agent_id"] for e in edges)
