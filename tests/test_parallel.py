import pytest
import hashlib
from langgraph.types import Send
from unittest.mock import MagicMock

from agentguard.state import _merge_results, _merge_usage, _merge_signal, AgentGuardState, GovernanceDecision
from agentguard.executor import RecursiveExecutor

# --------------------------------------------------------------------------- #
#  Reducer Unit Tests                                                          #
# --------------------------------------------------------------------------- #

def test_reducer_merge_results_no_overlap():
    left = {"agent1:hash1": "Result 1"}
    right = {"agent2:hash2": "Result 2"}
    merged = _merge_results(left, right)
    assert merged == {"agent1:hash1": "Result 1", "agent2:hash2": "Result 2"}
    
    # Check that it doesn't mutate original dicts
    assert left == {"agent1:hash1": "Result 1"}

def test_parallel_cost_accumulation():
    left = {"total_cost": 0.05}
    right = {"total_cost": 0.15}
    merged = _merge_usage(left, right)
    assert merged == {"total_cost": 0.20}

def test_parallel_signal_priority_merge():
    # Priority: REJECT > HITL_PENDING > OK
    assert _merge_signal("OK", "OK") == "OK"
    assert _merge_signal("OK", "HITL_PENDING") == "HITL_PENDING"
    assert _merge_signal("HITL_PENDING", "OK") == "HITL_PENDING"
    assert _merge_signal("HITL_PENDING", "REJECT") == "REJECT"
    assert _merge_signal("REJECT", "HITL_PENDING") == "REJECT"
    assert _merge_signal("REJECT", "REJECT") == "REJECT"

# --------------------------------------------------------------------------- #
#  Parallel Graph / Executor Tests                                             #
# --------------------------------------------------------------------------- #

def _mock_policy(allowed=True, hitl=False, reason="ok"):
    policy = MagicMock()
    decision = GovernanceDecision(
        action="test_action", allowed=allowed, reason=reason, cost=0.05, hitl_required=hitl,
    )
    policy.check_preflight.return_value = (allowed, decision)
    policy.check_step.return_value = (allowed, decision)
    return policy

def _make_executor(policy=None, max_depth=3) -> RecursiveExecutor:
    mem = MagicMock()
    mem.detect_loop.return_value = False
    mem.get_history.return_value = []

    reg = MagicMock()
    reg.get.return_value = {"role": "Test Agent"}

    executor = RecursiveExecutor(memory_manager=mem, registry=reg, max_depth=max_depth)
    if policy:
        executor.policy = policy
    
    executor.planner = MagicMock()
    
    return executor

def test_max_depth_skips_fan_out():
    executor = _make_executor(max_depth=3)
    state = {
        "all_edges": [{"task": "Task 1", "agent_id": "agent_1"}],
        "depth": 3,  # Max depth reached
    }
    sends = executor._edge_fan_out(state)
    assert len(sends) == 1
    assert sends[0].node == "collect_max_depth"

def test_parallel_subtasks_all_execute():
    executor = _make_executor()
    state = {
        "all_edges": [
            {"task": "Task 1", "agent_id": "agent_1"},
            {"task": "Task 2", "agent_id": "agent_2"}
        ],
        "depth": 1,
    }
    sends = executor._edge_fan_out(state)
    assert len(sends) == 2
    assert all(s.node == "worker" for s in sends)
    assert sends[0].arg["_current_edge"]["task"] == "Task 1"
    assert sends[1].arg["_current_edge"]["task"] == "Task 2"

def test_parallel_one_blocked_others_continue():
    # To test graph-level parallel execution correctly, we test _node_worker directly with different states,
    # then manually check that one blocked step doesn't block the other's result structure.
    executor_allowed = _make_executor(policy=_mock_policy(allowed=True))
    executor_blocked = _make_executor(policy=_mock_policy(allowed=False, reason="Forbidden"))
    
    state1 = {
        "depth": 2, # max_depth - 1 to avoid child graph recursive call
        "_current_edge": {"task": "Task 1", "agent_id": "agent_1"},
    }
    state2 = {
        "depth": 2,
        "_current_edge": {"task": "Task 2", "agent_id": "agent_2"},
    }
    
    res1 = executor_allowed._node_worker(state1, config=None)
    res2 = executor_blocked._node_worker(state2, config=None)
    
    # First is allowed
    assert "Completed: Task 1" in list(res1["results"].values())[0]
    
    # Second is blocked
    assert "BLOCKED: Forbidden" in list(res2["results"].values())[0]

def test_parallel_trace_events_merged():
    executor = _make_executor()
    state = {
        "depth": 2,
        "_current_edge": {"task": "Task", "agent_id": "agent"},
    }
    res = executor._node_worker(state, config=None)
    assert len(res["trace_events"]) >= 2  # exec_subtasks node event + worker step event
    assert res["trace_events"][-1]["action"] == "invoke_agent"

def test_parallel_governance_decisions_merged():
    executor = _make_executor(policy=_mock_policy(allowed=True))
    state = {
        "depth": 2,
        "_current_edge": {"task": "Task", "agent_id": "agent"},
    }
    res = executor._node_worker(state, config=None)
    assert len(res["governance_decisions"]) == 1
    assert res["governance_decisions"][0]["allowed"] is True

def test_parallel_hitl_interrupts_graph():
    executor = _make_executor(policy=_mock_policy(allowed=False, hitl=True, reason="Needs review"))
    state = {
        "depth": 2,
        "_current_edge": {"task": "Task", "agent_id": "agent"},
    }
    
    # We expect _node_worker to return HITL_PENDING signal
    res = executor._node_worker(state, config=None)
    assert res["global_signal"] == "HITL_PENDING"
    assert "HITL_PENDING" in list(res["results"].values())[0]
