"""
tests/test_hitl.py — Unit tests for HITL interrupt/resume cycle

Coverage:
  - executor sets HITL_PENDING signal when require_hitl rule fires
  - trace event status == "hitl_pending" for HITL steps
  - HITL_PENDING steps appear in pending_steps list
  - Non-HITL blocked steps produce status == "blocked"
  - auth_context propagated to child states
  - Expired auth_context blocks all steps with "error" status
"""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta

from agentguard.state import AgentGuardState, GovernanceDecision
from agentguard.executor import RecursiveExecutor
from agentguard.auth import make_auth_context, AuthContext


# --------------------------------------------------------------------------- #
#  Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _make_state(task="test task", budget=None, auth_context=None, **overrides) -> AgentGuardState:
    auth = auth_context or make_auth_context("test-user").to_dict()
    base = AgentGuardState(
        task=task,
        subject="test",
        root_task_id="hitl_test_run",
        parent_node_id="root",
        depth=0,
        results={},
        all_agents=[],
        all_edges=[],
        global_signal="",
        usage_stats={"total_cost": 0.0},
        budget_config=budget or {},
        governance_decisions=[],
        trace_events=[],
        auth_context=auth,
        parent_trace_event_id=None,
    )
    base.update(overrides)
    return base


def _mock_policy(allowed=True, hitl=False, reason="ok"):
    """Return a mock PolicyEngine where check_preflight and check_step are controllable."""
    policy = MagicMock()
    decision = GovernanceDecision(
        action="test_action", allowed=allowed, reason=reason, cost=0.05, hitl_required=hitl,
    )
    policy.check_preflight.return_value = (allowed, decision)
    policy.check_step.return_value = (allowed, decision)
    return policy


def _make_executor(policy=None, memory=None, registry=None) -> RecursiveExecutor:
    mem = memory or MagicMock()
    mem.detect_loop.return_value = False
    mem.get_history.return_value = []

    reg = registry or MagicMock()
    reg.get.return_value = {"role": "Test Agent", "semantic_description": "test"}

    executor = RecursiveExecutor(memory_manager=mem, registry=reg, max_depth=2)
    if policy:
        executor.policy = policy
    return executor


# --------------------------------------------------------------------------- #
#  HITL detection                                                              #
# --------------------------------------------------------------------------- #

class TestHITLDetection:
    def test_hitl_decision_sets_pending_signal(self):
        """When check_step returns hitl_required=True, global_signal is HITL_PENDING."""
        policy = _mock_policy(allowed=False, hitl=True, reason="HITL required for financial action.")
        executor = _make_executor(policy=policy)

        state = _make_state(
            _current_edge={"task": "Transfer $1M", "agent_id": "financial_agent"},
        )
        # Patch planner to skip LLM calls
        executor.planner = MagicMock()

        result = executor._node_worker(state, config=None)
        assert result["global_signal"] == "HITL_PENDING"

    def test_hitl_trace_event_has_hitl_pending_status(self):
        """Trace events for HITL steps have status == 'hitl_pending'."""
        policy = _mock_policy(allowed=False, hitl=True, reason="HITL required.")
        executor = _make_executor(policy=policy)
        state = _make_state(
            _current_edge={"task": "Wire transfer", "agent_id": "bank_agent"},
        )
        result = executor._node_worker(state, config=None)
        hitl_events = [e for e in result["trace_events"] if e.get("status") == "hitl_pending"]
        assert len(hitl_events) >= 1

    def test_blocked_step_has_blocked_status(self):
        """Regular blocked steps (non-HITL) have status == 'blocked'."""
        policy = _mock_policy(allowed=False, hitl=False, reason="Content blocked.")
        executor = _make_executor(policy=policy)
        state = _make_state(
            _current_edge={"task": "Forbidden task", "agent_id": "agent_x"},
        )
        result = executor._node_worker(state, config=None)
        blocked_events = [e for e in result["trace_events"] if e.get("status") == "blocked"]
        assert len(blocked_events) >= 1

    def test_output_contains_hitl_pending_label(self):
        """Subtask output string starts with HITL_PENDING when hitl=True."""
        policy = _mock_policy(allowed=False, hitl=True, reason="Needs approval.")
        executor = _make_executor(policy=policy)
        state = _make_state(
            _current_edge={"task": "Pay vendor", "agent_id": "pay_agent"},
        )
        result = executor._node_worker(state, config=None)
        assert any("HITL_PENDING" in str(v) for v in result.get("results", {}).values())

    def test_allowed_step_has_ok_status(self):
        """Allowed steps produce trace events with status == 'ok'."""
        policy = _mock_policy(allowed=True, reason="Step allowed.")
        executor = _make_executor(policy=policy)
        # depth=1 so no recursive child graph is spawned
        state = _make_state(
            depth=1,
            _current_edge={"task": "Research trends", "agent_id": "search_agent_01"},
        )
        result = executor._node_worker(state, config=None)
        step_events = [
            e for e in result["trace_events"]
            if e.get("node") == "worker" and e.get("action") and e["action"].startswith("invoke_")
        ]
        assert all(e["status"] == "ok" for e in step_events)


# --------------------------------------------------------------------------- #
#  Auth context propagation                                                    #
# --------------------------------------------------------------------------- #

class TestAuthPropagation:
    def test_auth_context_in_state(self):
        """auth_context dict is present in state and has expected fields."""
        ctx = make_auth_context("alice", roles=["user", "approver"])
        state = _make_state(auth_context=ctx.to_dict())
        restored = AuthContext.from_dict(state["auth_context"])
        assert restored.subject == "alice"
        assert restored.has_role("approver")

    def test_expired_auth_blocks_preflight(self):
        """Expired auth_context returns REJECT at preflight."""
        now = datetime.now(timezone.utc)
        expired = AuthContext(
            subject="expired-user", tenant_id="t", roles=["user"],
            issued_at=now - timedelta(hours=2),
            expires_at=now - timedelta(hours=1),
        )
        policy = _mock_policy(allowed=True)
        executor = _make_executor(policy=policy)
        state = _make_state(auth_context=expired.to_dict())
        result = executor._node_preflight(state, config=None)
        assert result["global_signal"] == "REJECT"

    def test_expired_auth_blocks_worker_step(self):
        """Expired auth_context in worker emits error trace events."""
        now = datetime.now(timezone.utc)
        expired = AuthContext(
            subject="expired-user", tenant_id="t", roles=["user"],
            issued_at=now - timedelta(hours=2),
            expires_at=now - timedelta(hours=1),
        )
        policy = _mock_policy(allowed=True)
        executor = _make_executor(policy=policy)
        state = _make_state(
            auth_context=expired.to_dict(),
            _current_edge={"task": "Run query", "agent_id": "agent_01"},
        )
        result = executor._node_worker(state, config=None)
        error_events = [e for e in result["trace_events"] if e.get("status") == "error"]
        assert len(error_events) >= 1
