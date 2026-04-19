import pytest
import uuid
from agentguard.state import make_initial_state, AgentGuardState

def test_make_initial_state_defaults():
    """Test that make_initial_state populates correct defaults."""
    task = "test task"
    state = make_initial_state(task=task)
    
    assert state["task"] == task
    assert state["subject"] == "anonymous"
    assert isinstance(state["root_task_id"], str)
    # Check if it's a valid UUID
    uuid.UUID(state["root_task_id"])
    
    assert state["depth"] == 0
    assert state["parent_node_id"] == "root"
    assert state["results"] == {}
    assert state["all_agents"] == []
    assert state["all_edges"] == []
    assert state["global_signal"] == "OK"
    assert state["usage_stats"] == {"total_cost": 0.0}
    assert state["budget_config"] == {}
    assert state["governance_decisions"] == []
    assert state["trace_events"] == []
    assert state["auth_context"] == {}
    assert state["parent_trace_event_id"] is None
    assert state["_current_edge"] is None

def test_make_initial_state_overrides():
    """Test that make_initial_state respects provided overrides."""
    task = "override task"
    subject = "user@example.com"
    budget = {"max_cost_usd": 10.0}
    run_id = "custom_id"
    auth = {"subject": "user@example.com"}
    
    state = make_initial_state(
        task=task,
        subject=subject,
        budget_config=budget,
        root_task_id=run_id,
        auth_context=auth,
        depth=1,
        parent_node_id="child_node"
    )
    
    assert state["task"] == task
    assert state["subject"] == subject
    assert state["root_task_id"] == run_id
    assert state["budget_config"] == budget
    assert state["auth_context"] == auth
    assert state["depth"] == 1
    assert state["parent_node_id"] == "child_node"

def test_make_initial_state_empty_auth():
    """Test that auth_context defaults to empty dict."""
    state = make_initial_state(task="test")
    assert state["auth_context"] == {}
