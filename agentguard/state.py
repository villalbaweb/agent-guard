from typing import TypedDict, Dict, List, Any


class GovernanceDecision(TypedDict):
    action: str
    allowed: bool
    reason: str
    cost: float
    hitl_required: bool  # True when a rule fires require_hitl instead of block


class AgentGuardState(TypedDict):
    task: str
    subject: str
    root_task_id: str
    parent_node_id: str
    depth: int
    results: Dict[str, str]
    all_agents: List[Dict[str, Any]]
    all_edges: List[Dict[str, Any]]
    global_signal: str          # "OK" | "INTERRUPT" | "RETRY" | "REJECT" | "HITL_PENDING"
    usage_stats: Dict[str, float]
    budget_config: Dict[str, float]
    governance_decisions: List[GovernanceDecision]
