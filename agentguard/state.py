from typing import TypedDict, Dict, List, Any, Optional, Annotated
import operator

class GovernanceDecision(TypedDict):
    action: str
    allowed: bool
    reason: str
    cost: float
    hitl_required: bool  # True when a rule fires require_hitl instead of block

def _merge_results(left: Dict, right: Dict) -> Dict:
    """Shallow merge — parallel workers write non-overlapping keys."""
    merged = dict(left)
    merged.update(right)
    return merged

def _merge_usage(left: Dict, right: Dict) -> Dict:
    """Sum total_cost across parallel workers."""
    return {"total_cost": left.get("total_cost", 0.0) + right.get("total_cost", 0.0)}

def _merge_signal(left: str, right: str) -> str:
    """Priority: REJECT > HITL_PENDING > any other."""
    priority = {"REJECT": 3, "HITL_PENDING": 2}
    if priority.get(right, 0) > priority.get(left, 0):
        return right
    return left

class AgentGuardState(TypedDict):
    task: str
    subject: str
    root_task_id: str
    parent_node_id: str
    depth: int
    results: Annotated[Dict[str, str], _merge_results]
    all_agents: Annotated[List[Dict[str, Any]], operator.add]
    all_edges: Annotated[List[Dict[str, Any]], operator.add]
    global_signal: Annotated[str, _merge_signal]
    usage_stats: Annotated[Dict[str, float], _merge_usage]
    budget_config: Dict[str, float]
    governance_decisions: Annotated[List[GovernanceDecision], operator.add]
    trace_events: Annotated[List[Dict[str, Any]], operator.add]
    auth_context: Dict[str, Any]         # JWT-derived caller identity (Step C)
    parent_trace_event_id: Optional[str] # event_id of the parent graph's execute_subtasks event
    _current_edge: Optional[Dict[str, Any]] # injected during Send for parallel workers
