from typing import TypedDict, Dict, List, Any

class GovernanceDecision(TypedDict):
    action: str
    allowed: bool
    reason: str
    cost: float

class AgentGuardState(TypedDict):
    task: str
    subject: str
    root_task_id: str
    parent_node_id: str
    depth: int
    results: Dict[str, str]
    all_agents: List[Dict[str, Any]]
    all_edges: List[Dict[str, Any]]
    global_signal: str  # e.g., "OK", "INTERRUPT", "RETRY"
    usage_stats: Dict[str, float]
    budget_config: Dict[str, float]
    governance_decisions: List[GovernanceDecision]
