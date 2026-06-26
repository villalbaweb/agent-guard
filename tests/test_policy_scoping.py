import pytest
import os
import yaml
from agentguard.policy import PolicyEngine
from agentguard.memory import MemoryManager
from agentguard.state import make_initial_state

@pytest.fixture
def memory():
    return MemoryManager()

@pytest.fixture
def policy_engine(memory):
    pe = PolicyEngine(memory)
    pe.llm = None
    return pe

def test_policy_isolation(policy_engine):
    """Verify that different policy_ids load different rules and budgets."""
    os.makedirs("policies", exist_ok=True)
    
    # Setup Tenant A: High budget
    policy_a = {
        "budget": {"max_cost_usd": 100.0, "per_step_limit_usd": 50.0},
        "content_rules": []
    }
    with open("policies/tenant_a.yaml", "w") as f:
        yaml.dump(policy_a, f)
        
    # Setup Tenant B: Very low budget
    policy_b = {
        "budget": {"max_cost_usd": 1.0, "per_step_limit_usd": 0.5},
        "content_rules": []
    }
    with open("policies/tenant_b.yaml", "w") as f:
        yaml.dump(policy_b, f)

    # Test Case 1: $5 step with Tenant A (should be ALLOWED)
    state_a = make_initial_state(task="test", policy_id="tenant_a")
    allowed_a, decision_a = policy_engine.check_step(state_a, action="tool_call", intent="do stuff", cost_estimate=5.0)
    assert allowed_a is True
    assert "Step allowed" in decision_a["reason"]

    # Test Case 2: $5 step with Tenant B (should be BLOCKED)
    state_b = make_initial_state(task="test", policy_id="tenant_b")
    allowed_b, decision_b = policy_engine.check_step(state_b, action="tool_call", intent="do stuff", cost_estimate=5.0)
    assert allowed_b is False
    assert "exceeds per-step limit" in decision_b["reason"]

def test_policy_content_rules_isolation(policy_engine):
    """Verify that content rules are isolated per tenant."""
    os.makedirs("policies", exist_ok=True)
    
    # Setup Tenant A: Block 'crypto'
    policy_a = {
        "content_rules": [
            {"id": "block_crypto", "field": "intent", "operator": "contains", "values": ["crypto"], "action": "block", "reason": "No crypto."}
        ]
    }
    with open("policies/tenant_a.yaml", "w") as f:
        yaml.dump(policy_a, f)
        
    # Setup Tenant B: Block 'politics'
    policy_b = {
        "content_rules": [
            {"id": "block_politics", "field": "intent", "operator": "contains", "values": ["politics"], "action": "block", "reason": "No politics."}
        ]
    }
    with open("policies/tenant_b.yaml", "w") as f:
        yaml.dump(policy_b, f)

    # Tenant A blocks crypto but allows politics
    state_a = make_initial_state(task="investigate crypto", policy_id="tenant_a")
    allowed_a1, _ = policy_engine.check_preflight(state_a)
    assert allowed_a1 is False
    
    state_a2 = make_initial_state(task="investigate politics", policy_id="tenant_a")
    allowed_a2, _ = policy_engine.check_preflight(state_a2)
    assert allowed_a2 is True

    # Tenant B blocks politics but allows crypto
    state_b1 = make_initial_state(task="investigate politics", policy_id="tenant_b")
    allowed_b1, _ = policy_engine.check_preflight(state_b1)
    assert allowed_b1 is False
    
    state_b2 = make_initial_state(task="investigate crypto", policy_id="tenant_b")
    allowed_b2, _ = policy_engine.check_preflight(state_b2)
    assert allowed_b2 is True

def test_policy_fallback(policy_engine):
    """Verify fallback to global policy if policy_id is None."""
    # Ensure current global policy (policy.yaml) is present or use defaults
    state_none = make_initial_state(task="test", policy_id=None)
    allowed, decision = policy_engine.check_step(state_none, action="test", intent="test", cost_estimate=0.1)
    # If no policy.yaml exists, it should use _DEFAULT_POLICY ($10 total, $1 step)
    assert allowed is True

def test_path_traversal_protection(policy_engine):
    """Verify that path traversal in policy_id is blocked by os.path.basename."""
    # Attempting to load a file outside policies/
    state_malicious = make_initial_state(task="test", policy_id="../policy.yaml")
    # policy.py uses os.path.basename("../policy.yaml") -> "policy.yaml"
    # then os.path.join("policies", "policy.yaml")
    # This should fail to find policies/policy.yaml and fallback to default or fail gracefully.
    allowed, decision = policy_engine.check_preflight(state_malicious)
    assert allowed is True # Fallback to default policy
