# Parallel Subtask Execution via LangGraph Send + Reducers

Migrate `RecursiveExecutor` from a serial `for` loop to LangGraph's native `Send` API with `Annotated` reducer functions, enabling true parallel subtask dispatch without distributed locks.

## Background

The current `_node_execute_subtasks` ([executor.py:L198–377](file:///d:/Git/agent-guard/agentguard/executor.py#L198-L377)) iterates over `all_edges` serially. The original plan (U-01/U-10) prescribed Redlock before enabling parallelism. However, LangGraph's superstep model already provides safe parallel state management:

1. Each parallel node receives an **immutable state snapshot**
2. Nodes return update dicts, never mutate shared state
3. LangGraph merges all updates at the superstep boundary using **reducer functions**

This eliminates the Redlock requirement entirely.

> [!IMPORTANT]
> **U-01 (Redlock) and U-10 (Parallel Execution Safety) will be resolved by this single change.** No distributed locking is needed.

---

## Resolved Decisions

| Decision | Choice | Rationale |
|:---------|:-------|:----------|
| HITL strategy | **Option A** — per-worker interrupt | Idiomatic LangGraph pattern; simpler; granular approval control |
| Results key convention | **`{agent_id}:{subtask_hash}`** | Guarantees uniqueness even with similarly-worded subtasks across agents |

---

## Recursive Parallelism & Trace Accuracy

Parallelism is **recursive at every depth level**. Each worker that spawns a child graph via `RecursiveExecutor.build_graph()` gets the same fan-out/worker pattern:

```
Root Graph (depth 0):
  preflight → decompose → plan → fan_out
    ├── worker_0 (subtask A) ──→ child graph (depth 1):
    │     preflight → decompose → plan → fan_out
    │       ├── worker_0 (sub-subtask A.1)  ← parallel
    │       └── worker_1 (sub-subtask A.2)  ← parallel
    │     → synthesize
    ├── worker_1 (subtask B) ──→ child graph (depth 1): ...  ← parallel
    └── worker_2 (subtask C) ──→ child graph (depth 1): ...  ← parallel
  → synthesize
```

**Trace causality is not just preserved — it becomes more accurate.** Today's serial trace falsely implies subtask B started after subtask A finished. The parallel trace correctly shows them as **sibling branches** from the same `parent_event_id`, reflecting the true execution topology. The `dump()` function in `trace.py` builds edges from `parent_event_id` links structurally — it doesn't depend on list order — so no changes are needed to trace serialization.

---

## Proposed Changes

### Phase 1 — State Schema (Annotated Reducers)

#### [MODIFY] [state.py](file:///d:/Git/agent-guard/agentguard/state.py)

Convert `AgentGuardState` from a plain `TypedDict` to use `Annotated` fields with reducer functions. This tells LangGraph how to merge parallel state updates safely.

| Field | Current Type | Reducer | Merge Behavior |
|:------|:-------------|:--------|:---------------|
| `trace_events` | `List[Dict]` | `operator.add` | Concatenate all events from parallel nodes |
| `governance_decisions` | `List[GovernanceDecision]` | `operator.add` | Concatenate all decisions |
| `results` | `Dict[str, str]` | `_merge_results` (custom) | Shallow dict merge — each worker writes a unique `{agent_id}:{subtask_hash}` key |
| `usage_stats` | `Dict[str, float]` | `_merge_usage` (custom) | Sum `total_cost` across workers |
| `all_agents` | `List[Dict]` | `operator.add` | Concatenate (rarely used in parallel, but safe) |
| `all_edges` | `List[Dict]` | `operator.add` | Concatenate |
| `global_signal` | `str` | `_merge_signal` (custom) | Priority merge: `REJECT` > `HITL_PENDING` > `OK` |

**Custom reducer functions** (defined in `state.py`):

```python
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
```

**State definition becomes:**

```python
from typing import Annotated
import operator

class AgentGuardState(TypedDict):
    task: str
    subject: str
    root_task_id: str
    parent_node_id: str
    depth: int
    results: Annotated[Dict[str, str], _merge_results]
    all_agents: Annotated[List[Dict], operator.add]
    all_edges: Annotated[List[Dict], operator.add]
    global_signal: Annotated[str, _merge_signal]
    usage_stats: Annotated[Dict[str, float], _merge_usage]
    budget_config: Dict[str, float]
    governance_decisions: Annotated[List[GovernanceDecision], operator.add]
    trace_events: Annotated[List[Dict[str, Any]], operator.add]
    auth_context: Dict[str, Any]
    parent_trace_event_id: Optional[str]
```

> [!WARNING]
> **Breaking change for downstream tests.** Every test that manually constructs `AgentGuardState` or directly appends to `governance_decisions` / `trace_events` will need updates. With reducers, nodes must **return** lists in their update dict — LangGraph handles the append.

---

### Phase 2 — Executor Refactor (Fan-Out / Worker / Collector)

#### [MODIFY] [executor.py](file:///d:/Git/agent-guard/agentguard/executor.py)

Replace the monolithic `_node_execute_subtasks` with three components:

```
plan ──→ fan_out ──→ [worker_0, worker_1, worker_2, ...] ──→ synthesize
                     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                     Parallel via Send — one per edge
```

##### 2a. Fan-Out Edge Function (`_edge_fan_out`)

A **conditional edge function** (not a node) that returns a list of `Send` objects. Each `Send` targets the `"worker"` node with a scoped payload for one subtask.

```python
from langgraph.types import Send

def _edge_fan_out(self, state: AgentGuardState) -> list[Send]:
    edges = state.get("all_edges", [])
    depth = state.get("depth", 0)

    if depth >= self.max_depth:
        # No parallel dispatch — go directly to synthesize
        return [Send("collect_max_depth", state)]

    sends = []
    for edge in edges:
        sends.append(Send("worker", {
            **state,
            "_current_edge": edge,  # private field scoped to this worker
        }))
    return sends
```

##### 2b. Worker Node (`_node_worker`)

Executes **one subtask**: auth check → policy check → (optional recursive child graph) → returns state update dict.

This is the body of the current `for edge in edges:` loop extracted into its own isolated function. Each worker:

1. Receives immutable state + `_current_edge`
2. Runs `PolicyEngine.check_step()` for that single edge
3. If allowed and `depth < max_depth - 1`, spawns a child `RecursiveExecutor` graph (unchanged from today)
4. Writes result to `results` under the key `{agent_id}:{subtask_hash[:8]}`
5. Returns an update dict with `results`, `usage_stats`, `governance_decisions`, `trace_events`, `global_signal`

LangGraph runs all workers in parallel within a single superstep, then merges their outputs using the reducers from Phase 1.

##### 2c. Graph Wiring

```python
def build_graph(self):
    graph = StateGraph(AgentGuardState)

    graph.add_node("preflight", self._node_preflight)
    graph.add_node("decompose", self._node_decompose)
    graph.add_node("plan", self._node_plan)
    graph.add_node("worker", self._node_worker)                    # NEW — per-subtask
    graph.add_node("collect_max_depth", self._node_max_depth)      # NEW — depth edge case
    graph.add_node("synthesize", self._node_synthesize)
    graph.add_node("reject", self._node_reject)

    graph.set_entry_point("preflight")
    graph.add_conditional_edges(
        "preflight", self._edge_post_preflight,
        {"continue": "decompose", "reject": "reject"},
    )
    graph.add_edge("decompose", "plan")
    graph.add_conditional_edges("plan", self._edge_fan_out)        # Send dispatch
    graph.add_edge("worker", "synthesize")
    graph.add_edge("collect_max_depth", "synthesize")
    graph.add_edge("synthesize", END)
    graph.add_edge("reject", END)
```

##### 2d. HITL Adaptation (Option A — Per-Worker Interrupt)

Each worker that hits a `require_hitl` rule calls `interrupt()` independently. LangGraph pauses the graph when *any* worker interrupts. On resume, only the interrupted worker re-executes; all other successfully completed workers' results are already merged into state.

- ✅ Natural, idiomatic LangGraph pattern
- ✅ No batching logic or custom collector needed
- ✅ Each HITL step gets its own `POST /approve` call — granular approval control

---

### Phase 3 — Node-Level Adjustments

#### [MODIFY] [executor.py](file:///d:/Git/agent-guard/agentguard/executor.py) — `_node_preflight`, `_node_decompose`, `_node_plan`

These nodes currently do `list(state.get("trace_events", []))` and manually append events. With `operator.add` reducers, they must instead return **only their new events** — the reducer handles concatenation.

**Before (current pattern — returns full replaced list):**
```python
trace_events = list(state.get("trace_events", []))  # copy entire list
# ... do work ...
trace_events.append(new_event)
return {"trace_events": trace_events}
```

**After (reducer pattern — returns delta only):**
```python
# ... do work ...
return {"trace_events": [new_event]}  # reducer appends to existing
```

Same pattern applies to `governance_decisions` in every node.

> [!NOTE]
> This is the single most pervasive change — every node and every test that touches `trace_events` or `governance_decisions` must switch from "copy-and-replace-full-list" to "return-delta-only".

#### [MODIFY] [executor.py](file:///d:/Git/agent-guard/agentguard/executor.py) — `_node_synthesize`

Currently reads from `state["results"]["subtasks_output"]`. With parallel workers, each worker writes its result under `{agent_id}:{subtask_hash}` (merged via `_merge_results`). The synthesize node must be updated to:

1. Read all subtask results from the merged `results` dict
2. Identify result entries by the `{agent_id}:{hash}` key format (filter out `final_answer`, `subtasks`, etc.)
3. Join all result values into `final_answer`

---

### Phase 4 — Loop Detection Adjustment

#### [MODIFY] [policy.py](file:///d:/Git/agent-guard/agentguard/policy.py) — `check_step()` loop detection

Currently calls `memory.detect_loop()` and `memory.record_thought()` which write to Redis during step execution. With parallel workers, multiple workers may call these concurrently within the same run.

**Risk:** Low. Each call is scoped by `run_id`, and Redis `SET`/`GET` are atomic. The thought history may have interleaved ordering from parallel workers, but this doesn't affect correctness — loop detection checks similarity against *any* recent thought, not a specific sequence.

**Mitigation:** No code change required for correctness. Document this behavior in the `MemoryManager` docstring.

---

### Phase 5 — Documentation Updates

#### [MODIFY] [unknown_items.md](file:///d:/Git/agent-guard/docs/unknown_items.md)

- **U-01**: Close as `✅ Resolved` — "Superseded by LangGraph superstep reducers. Each parallel worker receives an immutable state snapshot; updates are merged via reducer functions at the superstep boundary. No distributed locks needed."
- **U-10**: Close as `✅ Resolved` — "Implemented via LangGraph `Send` API. `_edge_fan_out` dispatches one `Send` per subtask; `_node_worker` executes independently. Reducers in `AgentGuardState` handle merge."

#### [MODIFY] [implementation_plan.md](file:///d:/Git/agent-guard/docs/implementation_plan.md)

- Update the architecture diagram to show the fan-out/worker pattern
- Remove Redlock references from "Known Limitations"
- Update milestone status to **Milestone 6 complete**

#### [MODIFY] [modernization_and_llm_integration.md](file:///d:/Git/agent-guard/docs/modernization_and_llm_integration.md)

- Update Section 8 (Remaining Gaps): remove "Replace serial `for` loop... requires U-01 Redlock first"
- Add note about the implemented parallel execution pattern and the `Send` + reducer approach

#### [MODIFY] [technical_specification_document.md](file:///d:/Git/agent-guard/docs/technical_specification_document.md)

- Update Section 5.1 (Orchestrator Graph Flow) diagram to show `plan → [worker × N] → synthesize`
- Update Section 5.3 (Recursive Worker Subgraph) — each `worker` node is the unit of parallelism

---

## Verification Plan

### Automated Tests

#### Existing tests (must all pass after refactor)

```bash
PYTHONPATH=. uv run pytest tests/test_trace.py -v
PYTHONPATH=. uv run pytest tests/test_hitl.py -v
PYTHONPATH=. uv run pytest tests/test_auth.py -v
PYTHONPATH=. uv run pytest tests/test_api.py -v
```

All existing tests will require updates to use the reducer-compatible pattern (return deltas, not full lists).

#### New tests to add in `tests/test_parallel.py`

| Test | What it validates |
|:-----|:------------------|
| `test_parallel_subtasks_all_execute` | Fan-out dispatches N workers, all produce results |
| `test_parallel_trace_events_merged` | `trace_events` from all workers appear in final state |
| `test_parallel_cost_accumulation` | `_merge_usage` sums costs from all parallel workers correctly |
| `test_parallel_governance_decisions_merged` | All per-worker decisions appear in `governance_decisions` |
| `test_parallel_one_blocked_others_continue` | One worker blocked by policy; others complete normally |
| `test_parallel_hitl_interrupts_graph` | A worker hitting `require_hitl` triggers graph-level interrupt |
| `test_parallel_signal_priority_merge` | `REJECT` takes priority over `OK` in `_merge_signal` |
| `test_reducer_merge_results_no_overlap` | `_merge_results` correctly merges non-overlapping `{agent_id}:{hash}` keys |
| `test_max_depth_skips_fan_out` | At `max_depth`, graph routes to `collect_max_depth` instead of workers |

### Manual Verification

- Run the full PoC (`uv run python agentguard/poc.py`) and confirm:
  - Multiple subtasks execute (visible in logs via `[ALLOWED]` messages)
  - Trace JSON shows parallel `worker` events sharing the same `parent_event_id`
  - Cost totals are correct across parallel branches
- Run via REST API (`POST /runs`) and confirm the trace endpoint returns the expected causal graph structure with sibling worker branches

### Load Test (Stretch Goal)

- Submit 3 concurrent runs with 3 subtasks each = 9 parallel workers
- Confirm no state corruption or trace event loss across runs
