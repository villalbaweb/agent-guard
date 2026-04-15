# AgentGuard — Architecture Reference

> Current MVP state: **Milestone 5 complete.** All core components implemented and tested.  
> Next milestone: Parallel execution via `Send` + vector registry (see `unknown_items.md`).

---

## System Architecture

AgentGuard uses a **Supervisor-Worker-Guard** pattern built on LangGraph:

```
User / REST Client
        │
        ▼
   FastAPI Backend  (backend/)
        │
        ▼
   RecursiveExecutor  (agentguard/executor.py)
   ┌────────────────────────────────────────────────────┐
   │  preflight → decompose → plan → execute → synthesize│
   │                    ↑ every step                     │
   │              PolicyEngine check                     │
   └────────────────────────────────────────────────────┘
        │                    │
        ▼                    ▼
   MemoryManager         LLM Provider
   (Redis / in-mem)      (Gemini / Claude / OpenAI)
```

### LangGraph Graph Nodes

| Node | Responsibility |
|:-----|:---------------|
| `preflight` | `PolicyEngine.check_preflight()` — blocks forbidden tasks before any LLM call |
| `decompose` | LLM breaks task into focused subtask strings (JSON output prompt) |
| `plan` | Intent extraction routes each subtask to the best registered agent |
| `execute_subtasks` | Per-step policy check → cost accumulation → optional recursive child graph |
| `synthesize` | Joins subtask outputs into `final_answer` |
| `reject` | Terminal node for preflight failures |

Child graphs inherit `root_task_id`, `auth_context`, and budget from the parent. Hard `max_depth` prevents runaway recursion.

---

## Core Components

### `agentguard/state.py` — Shared State
Single `AgentGuardState` TypedDict shared by all graph nodes and child graphs:

```python
class AgentGuardState(TypedDict):
    task: str
    subject: str
    root_task_id: str
    parent_node_id: str
    depth: int
    results: Dict[str, str]
    all_agents: List[Dict]
    all_edges: List[Dict]
    global_signal: str           # "OK" | "INTERRUPT" | "RETRY" | "REJECT" | "HITL_PENDING"
    usage_stats: Dict[str, float]
    budget_config: Dict[str, float]
    governance_decisions: List[GovernanceDecision]
    trace_events: List[Dict[str, Any]]   # append-only causal event log
    auth_context: Dict[str, Any]         # JWT-derived caller identity
    parent_trace_event_id: Optional[str] # causal link from parent graph
```

`GovernanceDecision` fields: `action`, `allowed`, `reason`, `cost`, `hitl_required`.  
`hitl_required=True` triggers a HITL pause rather than a hard block.

---

### `agentguard/policy.py` — Policy Engine
Evaluation order for every `check_step()` call:

1. Per-step cost ceiling (state `budget_config` overrides `policy.yaml`)
2. Cumulative budget check
3. Content rules — compiled `regex` / `contains`, first match wins
4. LLM semantic guard — structured prompt with explicit ALLOW bias for internal operations
5. Loop detection — delegates to `MemoryManager.detect_loop()`

Rule actions: `block` (hard deny), `allow` (explicit pass), `require_hitl` (pause for human approval).  
Policy hot-reload: `POST /policy/reload` calls `PolicyEngine.reload()` without dropping in-flight runs.

---

### `agentguard/memory.py` — Shared Epistemic Memory
Redis-backed with silent in-memory fallback.

| Redis Key | Contents |
|:----------|:---------|
| `run:{id}:history` | Thought history with embeddings for loop detection |
| `run:{id}:blueprints` | Cached execution plans |
| `run:{id}:state` | Full `AgentGuardState` snapshot at run completion |
| `run:{id}:trace` | Serialized trace JSON (served by `/runs/{id}/trace`) |

Loop detection: cosine similarity on cloud embeddings (configurable threshold + lookback window). Falls back to exact-match string comparison when embeddings are unavailable.

---

### `agentguard/trace.py` — Causal Dependency Graph
Append-only event log producing a machine-readable JSON audit trail per run:

```
schema_version: "1.0"
run_id / task / started_at / finished_at / total_cost_usd / final_status
events: [ TraceEvent, ... ]   ← causal order
edges:  [ {from: event_id, to: event_id}, ... ]  ← parent→child adjacency
```

Each `TraceEvent` carries: `event_id`, `parent_event_id`, `node`, `depth`, `timestamp`, `agent_id`, `action`, `intent`, `decision` (inlined `GovernanceDecision`), `cost_delta`, `cumulative_cost`, `status`.

Child graph events are merged into the parent via `trace_events.extend(child["trace_events"])`, producing a single unified causal tree per top-level run.

---

### `agentguard/auth.py` — Identity & JWT
`AuthContext` dataclass — propagated verbatim through all child subgraphs:

```python
@dataclass
class AuthContext:
    subject: str       # stable user/service id
    tenant_id: str
    roles: list[str]   # ["user", "approver", ...]
    issued_at: datetime
    expires_at: datetime
    token_id: str      # jti for revocation
```

- HS256 JWT (dev). RS256/JWKS deferred to Milestone 6.
- `_auth_expired()` checked at preflight and before every per-step edge.
- Every `GovernanceDecision.reason` is prefixed `[subject:X]` for attributable audit.
- `AGENTGUARD_NO_AUTH=1` bypasses auth entirely (dev/testing only).

---

### `agentguard/registry.py` — Capability Registry
Central "Yellow Pages" mapping agent IDs to metadata (role, semantic description, I/O schemas).  
`ExecutionPlanner` uses `search_by_intent()` to route subtasks to agents.

**Current:** In-memory, substring-match routing. Accurate for 2–5 agents.  
**Planned (U-09):** Redis-backed + vector similarity search for production scale.

---

### `backend/` — REST API Layer
Thin FastAPI facade over `agentguard/`. All orchestration logic stays in the core package.

| Method | Path | Auth | Description |
|:-------|:-----|:-----|:------------|
| `GET` | `/health` | None | Redis + LLM liveness probe |
| `POST` | `/runs` | Bearer / API key | Submit task (background); returns `run_id` |
| `GET` | `/runs/{id}` | Bearer / API key | Status, `final_answer`, cost, governance summary |
| `GET` | `/runs/{id}/trace` | Bearer / API key | Full trace JSON |
| `POST` | `/runs/{id}/approve` | Bearer + `approver` role | Approve/reject HITL-pending step |
| `GET` | `/policy` | Bearer / API key | Return active policy rules |
| `POST` | `/policy/reload` | Bearer / API key | Hot-reload policy without restart |

Run state machine: `queued → running → {completed | blocked | hitl_pending | error}`  
HITL resumes via `POST /approve` with an `approver`-role JWT.

---

## LLM & Embeddings Factory (`agentguard/llm.py`)

Provider resolution order (first configured key wins):

| Factory | Priority |
|:--------|:---------|
| `get_llm()` | Google AI Studio → Anthropic → OpenAI-compatible → Vertex AI |
| `get_embeddings()` | OpenAI → Google AI Studio → `None` (exact-match fallback) |

`OPENAI_BASE_URL` redirects to any OpenAI-compatible endpoint (OpenRouter, LiteLLM, Ollama, vLLM).

---

## Infrastructure

```yaml
# docker-compose.poc.yml
services:
  redis:   redis:7-alpine  →  port 6379  (state, traces, checkpointer)
  backend: Dockerfile.backend  →  port 8000  (FastAPI)
```

Start stack: `docker compose -f docker-compose.poc.yml up -d`  
Start API only: `uv run uvicorn backend.main:app --reload --port 8000`

---

## Known Limitations & Deferred Work

See `unknown_items.md` for the full open-items register. Key items:

| Item | Blocker |
|:-----|:--------|
| Vector similarity routing in Registry | Redis VSS migration (U-09) |
| RS256 / JWKS JWT verification | Production identity provider integration |
| Multi-tenant policy scoping | Per-request `policy_id` resolution (U-11) |
| MCP proxy integration | MCP framework selection (U-03) |
| Embedding cache | Redis hash-based caching to cut round-trip latency (U-08) |
ion | MCP framework selection (U-03) |
| Embedding cache | Redis hash-based caching to cut round-trip latency (U-08) |
