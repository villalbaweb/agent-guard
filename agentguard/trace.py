"""
trace.py — Causal Dependency Graph Builder
-------------------------------------------
Every node boundary and policy decision emits a TraceEvent that is appended
(append-only) to AgentGuardState.trace_events.  At run exit, dump() converts
the flat list into the canonical JSON schema consumed by the REST API and
downstream SIEM / audit tools.

Storage locations (resolved at call site):
  - Local disk : ./traces/{run_id}.json   (PoC default)
  - Redis key  : run:{run_id}:trace        (REST API — Step B)
"""
import json
import uuid
import logging
import os
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Callable

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
#  Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _make_event_id() -> str:
    return str(uuid.uuid4())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
#  Event factory                                                               #
# --------------------------------------------------------------------------- #

def new_event(
    run_id: str,
    node: str,
    depth: int,
    parent_event_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    action: Optional[str] = None,
    intent: Optional[str] = None,
    decision: Optional[Dict[str, Any]] = None,
    cost_delta: float = 0.0,
    cumulative_cost: float = 0.0,
    status: str = "ok",
    duration_ms: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
    llm_token_counts: Optional[Dict[str, int]] = None,
    latency_breakdown: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """Return a new trace event as a plain JSON-serializable dict.

    Args:
        run_id:             Equals root_task_id for all events in a run.
        node:               Graph node name: "preflight" | "decompose" | "plan" |
                            "execute_subtasks" | "synthesize" | "reject"
        depth:              Current recursion depth (0 = root graph).
        parent_event_id:    event_id of the causally preceding event, or None for
                            the root event.
        agent_id:           Registry ID of the executing agent (execute_subtasks only).
        action:             Policy action string (e.g. "invoke_search_agent_01").
        intent:             Human-readable intent passed to the policy engine.
        decision:           GovernanceDecision dict emitted by PolicyEngine.
        cost_delta:         USD cost attributed to this specific step.
        cumulative_cost:    Running total cost at event creation time.
        status:             "ok" | "blocked" | "hitl_pending" | "error"
        duration_ms:        Wall-clock duration of the node, if measured.
        metadata:           Free-form extras (subtask list, llm provider, etc.).
        llm_token_counts:   Token usage breakdown, e.g.
                            {"prompt_tokens": 512, "completion_tokens": 128, "total_tokens": 640}.
                            Populate at call sites that invoke an LLM to make cost visible.
        latency_breakdown:  Per-phase latency in ms, e.g.
                            {"llm_ms": 850, "policy_ms": 40, "overhead_ms": 12}.
                            Populate alongside duration_ms for fine-grained profiling.

    Returns:
        Dict with all TraceEvent fields, ready for json.dumps().
    """
    return {
        "event_id": _make_event_id(),
        "parent_event_id": parent_event_id,
        "run_id": run_id,
        "node": node,
        "depth": depth,
        "timestamp": _now_iso(),
        "duration_ms": duration_ms,
        "agent_id": agent_id,
        "action": action,
        "intent": intent,
        "decision": dict(decision) if decision else None,
        "cost_delta": cost_delta,
        "cumulative_cost": cumulative_cost,
        "status": status,
        "metadata": metadata or {},
        "llm_token_counts": llm_token_counts,
        "latency_breakdown": latency_breakdown,
    }


# --------------------------------------------------------------------------- #
#  Serializer                                                                  #
# --------------------------------------------------------------------------- #

def dump(
    state: Dict[str, Any],
    task: str = "",
    started_at: str = "",
    finished_at: Optional[str] = None,
    final_status: str = "completed",
    policy_version: str = "1.0",
    llm_providers: Optional[Dict[str, str]] = None,
    redactor: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Serialize state.trace_events into the canonical trace JSON schema.

    Args:
        state:          Final AgentGuardState after run completion.
        task:           Original task string (falls back to state["task"]).
        started_at:     ISO-8601 UTC timestamp of run start.
        finished_at:    ISO-8601 UTC timestamp of run end (defaults to now).
        final_status:   "completed" | "blocked" | "hitl_pending" | "error"
        policy_version: Version string of the loaded policy.yaml.
        llm_providers:  Dict mapping role → provider, e.g.
                        {"chat": "google:gemini-2.0-flash"}.
        redactor:       Optional callable applied to each event dict before
                        serialization — use for PII scrubbing.

    Returns:
        Dict conforming to schema_version 1.0.
    """
    events: List[Dict[str, Any]] = list(state.get("trace_events", []))
    finished = finished_at or _now_iso()

    total_duration_ms: Optional[int] = None
    if started_at and finished:
        try:
            start_dt = datetime.fromisoformat(started_at)
            end_dt = datetime.fromisoformat(finished)
            total_duration_ms = int((end_dt - start_dt).total_seconds() * 1000)
        except Exception:
            pass

    if redactor:
        events = [redactor(e) for e in events]

    # Build adjacency list for graph visualization
    # 1. Map event_id to all its direct child event_ids
    children_map = {}
    for e in events:
        parent = e.get("parent_event_id")
        if parent:
            children_map.setdefault(parent, []).append(e["event_id"])

    # 2. Build edges: parent-child causal relationships for non-synthesize nodes
    edges = []
    for e in events:
        parent = e.get("parent_event_id")
        # Synthesize nodes converge all parallel branches, handled below
        if parent and e["node"] != "synthesize":
            edges.append({"from": parent, "to": e["event_id"]})

    # 3. For each synthesize event, link all leaf descendants of its sibling worker branches
    synthesize_events = [e for e in events if e["node"] == "synthesize"]
    for s in synthesize_events:
        depth = s["depth"]
        # Find the plan event at the same depth that occurred before S
        plan_events = [
            e for e in events 
            if e["node"] == "plan" 
            and e["depth"] == depth
        ]
        s_index = events.index(s)
        preceding_plans = [e for e in plan_events if events.index(e) < s_index]
        
        if preceding_plans:
            plan_event = preceding_plans[-1]
            # Find direct worker children of this plan event
            workers = [
                e for e in events 
                if e["node"] == "worker" 
                and e["depth"] == depth 
                and e.get("parent_event_id") == plan_event["event_id"]
            ]
            for w in workers:
                # Traverse descendants to find all final leaf events of this branch
                leaves = []
                queue = [w["event_id"]]
                while queue:
                    curr = queue.pop(0)
                    children = children_map.get(curr, [])
                    if not children:
                        leaves.append(curr)
                    else:
                        queue.extend(children)
                
                # Connect each leaf of the branch to the synthesize event S
                for leaf in leaves:
                    edges.append({"from": leaf, "to": s["event_id"]})
        else:
            # Fallback: link the last event before synthesize to it
            if s_index > 0:
                edges.append({"from": events[s_index - 1]["event_id"], "to": s["event_id"]})

    return {
        "schema_version": "1.1",
        "run_id": state.get("root_task_id", "unknown"),
        "task": task or state.get("task", ""),
        "started_at": started_at,
        "finished_at": finished,
        "total_duration_ms": total_duration_ms,
        "total_cost_usd": state.get("usage_stats", {}).get("total_cost", 0.0),
        "final_status": final_status,
        "final_answer": state.get("results", {}).get("final_answer"),
        "policy_version": policy_version,
        "llm_providers": llm_providers or {},
        "events": events,
        "edges": edges,
    }


def write_to_disk(trace: Dict[str, Any], directory: str = "./traces") -> str:
    """Write a trace dict to ./traces/{run_id}.json.

    Creates the directory if it does not exist.

    Returns:
        Absolute path of the written file.
    """
    os.makedirs(directory, exist_ok=True)
    run_id = trace.get("run_id", "unknown")
    path = os.path.join(directory, f"{run_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(trace, f, indent=2, default=str)
    logger.info(f"TraceBuilder: wrote trace → {path}")
    return path
