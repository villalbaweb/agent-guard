# AgentGuard: Next Steps Implementation Plan

> **Status (2026-04-10): Steps A, B, and C are fully COMPLETE.** All acceptance criteria below are checked off. See `implementation_report.md` for the detailed delivery report.

This document lays out the concrete implementation plan for the next three milestones on the AgentGuard roadmap, in execution order:

1. **Step A — Causal Dependency Graph Export** (Phase 4 closeout) ✅
2. **Step B — REST API (FastAPI)** (Milestone 5, customer interface) ✅
3. **Step C — HITL Wiring & JWT Identity Propagation** (resolves U-04 and U-05) ✅

Each step is designed so the next one builds on its output:
- Step A produces the JSON trace artifact that Step B will expose over HTTP.
- Step B provides the transport layer (`/runs`, `/runs/{id}/approve`) that Step C's HITL pause/resume cycle needs.
- Step C uses the `auth_context` threaded through the same `AgentGuardState` that Step A already serializes.

---

## Step A — Causal Dependency Graph Export

### Goal
Produce a machine-readable JSON trace per run that captures the full causal chain of nodes, policy decisions, budgets, and results. This closes the audit-trail wedge (EU AI Act, SOX retention) and gives downstream consumers (REST API, UI, external SIEM) a single canonical artifact.

### Files to Create
- `agentguard/trace.py` — trace builder and JSON serializer
- `tests/test_trace.py` — unit tests for serializer + round-trip

### Files to Modify
- `agentguard/state.py` — add `trace_events: List[TraceEvent]` field
- `agentguard/executor.py` — emit `TraceEvent` entries at each node boundary and after each `check_step`
- `agentguard/poc.py` — write trace to `./traces/{root_task_id}.json` at end of run
- `pyproject.toml` — no new deps (stdlib `json`, `uuid`, `datetime`)

### Data Model

```python
# agentguard/trace.py
class TraceEvent(TypedDict):
    event_id: str               # uuid4
    parent_event_id: Optional[str]
    run_id: str                 # == root_task_id
    node: str                   # "preflight" | "decompose" | "plan" | "execute_subtasks" | ...
    depth: int
    timestamp: str              # ISO-8601 UTC
    duration_ms: Optional[int]
    agent_id: Optional[str]     # for execute steps
    action: Optional[str]
    intent: Optional[str]
    decision: Optional[GovernanceDecision]
    cost_delta: float
    cumulative_cost: float
    status: str                 # "ok" | "blocked" | "hitl_pending" | "error"
    metadata: Dict[str, Any]    # free-form: subtask list, llm provider, prompt hash, etc.
```

### Trace JSON Schema (exported artifact)

```json
{
  "schema_version": "1.0",
  "run_id": "run_abc123",
  "task": "Analyze renewable energy trends",
  "started_at": "2026-04-10T14:22:01Z",
  "finished_at": "2026-04-10T14:22:38Z",
  "total_duration_ms": 37000,
  "total_cost_usd": 0.12,
  "final_status": "completed",
  "final_answer": "...",
  "policy_version": "1.0",
  "llm_providers": {"chat": "google:gemini-3.1-pro", "embeddings": "openai:text-embedding-3-small"},
  "events": [ /* list of TraceEvent, in causal order */ ],
  "edges": [ /* parent_event_id -> event_id adjacency list */ ]
}
```

### Key Implementation Decisions

1. **Causal linking via parent_event_id** — each child graph spawn creates a trace event whose `parent_event_id` is the parent's `execute_subtasks` event. This gives us a true tree (not just a flat log) suitable for graph visualization.
2. **Policy decisions inlined** — every `GovernanceDecision` is embedded in its parent event, not stored separately. One object per event = simpler consumers.
3. **Append-only in state** — `trace_events` is treated like `governance_decisions`: each node returns `{"trace_events": [...existing, new_event]}`. No mutation.
4. **Serialization at run exit** — `TraceBuilder.dump(state)` converts the flat list into the nested JSON schema above, resolves parent/child edges, and writes to disk.
5. **Storage for now:** local `./traces/{run_id}.json`. Redis-backed storage deferred until REST API needs multi-instance access (Step B).
6. **Redaction hook** — `TraceBuilder.dump(state, redactor=fn)` accepts an optional callable so PII filters can wipe fields before persistence. Default: no-op.

### Testing Approach
- **Unit:** `test_trace.py` — verify `TraceBuilder` correctly linearizes events, builds edges, handles missing parents, handles empty runs.
- **Integration:** Re-run existing `poc.py` scenarios and assert each produces a valid JSON file matching the schema. Verify blocked scenarios still emit events (preflight → reject).
- **Schema validation:** Add one golden-file test per PoC scenario; compare structural keys (not values — costs/timestamps are non-deterministic).

### Acceptance Criteria
- [x] `AgentGuardState.trace_events` populated end-to-end in all 5 PoC scenarios
- [x] `./traces/{run_id}.json` produced for every run, conforming to schema
- [x] Every `governance_decision` in the state has a corresponding event in the trace
- [x] Trace JSON is valid and parseable by `json.loads()`
- [x] At least one test asserts parent/child causality for a recursive child graph

---

## Step B — REST API (FastAPI)

### Goal
Wrap the `RecursiveExecutor` behind a small, stateless HTTP API so AgentGuard can be consumed by non-Python clients and sold as a product. The API is a thin facade — all orchestration logic stays in `agentguard/`.

### Files to Create
- `backend/__init__.py`
- `backend/main.py` — FastAPI `app`, router wiring
- `backend/routes/runs.py` — `POST /runs`, `GET /runs/{id}`, `GET /runs/{id}/trace`, `POST /runs/{id}/approve`
- `backend/routes/policy.py` — `GET /policy`, `POST /policy/reload`
- `backend/routes/health.py` — `GET /health`
- `backend/schemas.py` — Pydantic request/response models
- `backend/store.py` — run state persistence (Redis-backed, via existing `MemoryManager`)
- `backend/dependencies.py` — dependency injection for executor, policy, memory
- `tests/test_api.py` — httpx TestClient suite
- `Dockerfile.backend` — production image
- `docker-compose.poc.yml` — add `backend` service

### Files to Modify
- `pyproject.toml` — add `fastapi>=0.115.0`, `uvicorn[standard]>=0.32.0`, `pydantic>=2.0`
- `agentguard/memory.py` — add `store_run(run_id, state)` and `get_run(run_id)` helpers
- `docker-compose.poc.yml` — add backend service on port 8000

### API Surface

| Method | Path | Purpose |
| :--- | :--- | :--- |
| `POST` | `/runs` | Submit a new task. Returns `{run_id, status}` immediately (background execution) |
| `GET` | `/runs/{id}` | Fetch run status, final_answer, usage_stats, governance summary |
| `GET` | `/runs/{id}/trace` | Download full causal graph JSON (from Step A) |
| `POST` | `/runs/{id}/approve` | Approve a `HITL_PENDING` step; resumes the graph (requires Step C) |
| `GET` | `/policy` | Return currently loaded `policy.yaml` contents |
| `POST` | `/policy/reload` | Hot-reload the policy file without restart |
| `GET` | `/health` | Redis + LLM provider liveness probe |

### Request/Response Schemas

```python
# backend/schemas.py
class RunCreateRequest(BaseModel):
    task: str
    subject: str = "default"
    budget_override: dict[str, float] | None = None
    max_depth: int = 3

class RunCreateResponse(BaseModel):
    run_id: str
    status: Literal["queued", "running"]
    submitted_at: datetime

class RunStatusResponse(BaseModel):
    run_id: str
    status: Literal["running", "completed", "blocked", "hitl_pending", "error"]
    final_answer: str | None
    total_cost_usd: float
    decisions_summary: dict[str, int]  # {"allow": 5, "block": 1, "hitl": 0}
    trace_url: str  # "/runs/{id}/trace"
```

### Key Implementation Decisions

1. **Background execution** — `POST /runs` kicks off the graph via `BackgroundTasks` (PoC) or Celery/RQ (production). Run state is written to Redis under `run:{id}:state` as the graph progresses.
2. **Stateless API server** — all persistence lives in Redis. Scale by running multiple FastAPI workers behind a load balancer. No in-process run registry.
3. **Trace artifact delivery** — `/runs/{id}/trace` reads the JSON produced in Step A. Start by writing traces to Redis (`run:{id}:trace`) instead of local disk so any worker can serve them.
4. **Policy hot-reload** — `POST /policy/reload` calls `PolicyEngine.reload()` on the shared singleton. Return 409 if a reload is already in progress.
5. **Authentication** — Phase 1 uses a static `AGENTGUARD_API_KEY` header check. JWT-based auth arrives in Step C.
6. **OpenAPI schema** — FastAPI auto-generates `/docs` and `/openapi.json`. This is the customer-facing API contract.
7. **Error envelope** — consistent `{error: {code, message, details}}` shape for all 4xx/5xx, mapped from internal exceptions in a single middleware.

### Testing Approach
- **Unit:** `test_api.py` with `httpx.AsyncClient` + FastAPI `TestClient`. Mock `RecursiveExecutor` so tests run without LLM/Redis.
- **Integration:** Spin up the full `docker-compose.poc.yml` (Redis + backend), issue real requests against each PoC scenario, assert status + trace retrieval.
- **Contract:** Snapshot-test the generated `openapi.json` to catch breaking API changes.
- **Load:** Deferred to Phase 5 (10 concurrent runs, measure p95 latency).

### Acceptance Criteria
- [x] `uv run uvicorn backend.main:app` serves a working API on :8000
- [x] All 5 PoC scenarios runnable via `POST /runs` with matching results
- [x] `/runs/{id}/trace` returns valid JSON matching Step A schema
- [x] `POST /policy/reload` reloads rules without dropping in-flight runs
- [x] `docker-compose.poc.yml up` brings up Redis + backend + healthcheck green
- [x] OpenAPI docs render at `/docs`

---

## Step C — HITL Wiring & JWT Identity Propagation

### Goal
Make `require_hitl` actually pause the graph and resume after external approval (resolves **U-04**), and propagate a verifiable caller identity through every subgraph (resolves **U-05**). These are coupled because a HITL approval needs to be attributable to a specific human identity.

### Files to Create
- `agentguard/auth.py` — JWT generation/verification, `AuthContext` dataclass
- `tests/test_auth.py` — JWT round-trip, signature failure, expiry
- `tests/test_hitl.py` — interrupt/resume cycle with mock executor

### Files to Modify
- `agentguard/state.py` — add `auth_context: Dict[str, Any]` field
- `agentguard/executor.py` — emit LangGraph `interrupt()` on `hitl_required` decisions; thread `auth_context` into child states
- `agentguard/policy.py` — include `auth_context.subject` in audit log of every `GovernanceDecision`
- `backend/routes/runs.py` — `POST /runs/{id}/approve` resumes via LangGraph checkpointer
- `backend/dependencies.py` — JWT bearer token dependency; populate `AuthContext` from request
- `pyproject.toml` — add `pyjwt>=2.9.0`
- `.env.example` — document `AGENTGUARD_JWT_SECRET`, `AGENTGUARD_JWT_ISSUER`

### Data Model

```python
# agentguard/auth.py
@dataclass
class AuthContext:
    subject: str            # stable user/service id
    tenant_id: str          # for multi-tenancy
    roles: list[str]        # ["admin", "approver", ...]
    issued_at: datetime
    expires_at: datetime
    token_id: str           # jti, for revocation
```

JWT claims: `sub`, `tenant`, `roles`, `iat`, `exp`, `jti`, `iss`.

### HITL Flow

```
1. POST /runs              ──► executor starts graph with LangGraph checkpointer
2. check_step fires        ──► require_hitl rule matches
3. executor calls          ──► interrupt({subtask, reason, decision})
4. graph pauses            ──► checkpointer persists state to Redis
5. API returns             ──► status="hitl_pending", pending_step={...}
6. human reviews via UI    ──► POST /runs/{id}/approve {event_id, decision}
7. backend validates       ──► approver JWT has "approver" role
8. backend calls           ──► graph.resume(run_id, command=Command(resume={...}))
9. executor continues      ──► cost accumulates, trace event logged with approver subject
```

### Key Implementation Decisions

1. **LangGraph checkpointer** — use `langgraph.checkpoint.redis.RedisSaver` so interrupts survive process restarts. Shared with Step B's Redis.
2. **Interrupt payload** — `interrupt({event_id, subtask, agent_id, reason, required_role})` so the UI can show context and the approver knows what role is needed.
3. **Approval authorization** — `/runs/{id}/approve` requires the JWT `roles` claim to include the `required_role` from the interrupt. Hardcoded default: `approver`.
4. **Identity audit trail** — every `GovernanceDecision` emitted after resume includes `approved_by: subject` in the reason field, and the trace event records the approver JWT `jti` for non-repudiation.
5. **Child graph propagation** — when spawning child state in `executor.py`, copy `auth_context` verbatim. Children inherit the root caller's identity.
6. **JWT secret management** — HS256 for dev (`AGENTGUARD_JWT_SECRET` env var). RS256 with JWKS endpoint for production; defer to Phase 5.
7. **Expiry check** — each `check_step()` validates that `auth_context.expires_at` is in the future. Expired tokens block the step with reason "auth_context expired".
8. **Fail-closed default** — if `auth_context` is missing or invalid on a non-health endpoint, return 401 before touching the executor.

### Testing Approach
- **Unit:** `test_auth.py` — JWT encode/decode, expiry, bad signature, missing claims.
- **Unit:** `test_hitl.py` — mock `RecursiveExecutor`, trigger `require_hitl`, verify `interrupt()` called, resume with approval, assert trace contains approver subject.
- **Integration:** End-to-end via REST API. Issue `POST /runs` with a task that hits `hitl_financial_transactions` rule, assert `hitl_pending` status, `POST /runs/{id}/approve` with valid JWT, assert completion.
- **Negative:** Approval with wrong role → 403. Approval with expired JWT → 401. Approval on non-pending run → 409.

### Acceptance Criteria
- [x] `require_hitl` rule actually pauses a real graph run (not just sets a field)
- [x] Run state survives an API process restart (checkpointer proven)
- [x] `/runs/{id}/approve` resumes the graph and the run reaches `completed`
- [x] Every resumed step's trace event includes `approved_by` subject
- [x] Child subgraphs see the same `auth_context` as the root
- [x] Expired JWTs cannot start a run and cannot approve a pending one
- [x] Test suite covers JWT happy path, role mismatch, expiry, and full HITL cycle
- [x] Update every relevant doc within @docs to reflect the changes in this plan

---

## Dependencies & Sequencing

```
Step A (Trace) ─────┬─► Step B (REST API) ─────► Step C (HITL + JWT)
                    │          │
                    │          └─► Needs checkpointer in Redis (from Step C)
                    │
                    └─► Exposed via /runs/{id}/trace in Step B
```

- **Step A is standalone.** Can ship independently. No new deps.
- **Step B depends on Step A** for the trace endpoint. Trivially mockable if A slips.
- **Step C depends on Step B** for the approval endpoint. The HITL flow is unusable without an out-of-process way to deliver the approval signal — that's what the REST API is for.

## Out of Scope for These Three Steps

Tracked in `unknown_items.md`, deferred:
- **U-01 Redlock** — not needed while executor is still serial
- **U-03 MCP proxy** — waiting on MCP framework selection
- **U-06 cost attribution for cached blueprints** — blueprint cache not yet exercised by PoC
- **U-08 embedding cache** — optimization, not correctness
- **U-09 registry vector search** — Registry persistence migration is a separate milestone
- **U-10 parallel execution via `Send`** — requires U-01 first

---

*Referenced Documents:*
- *`implementation_plan.md` — Phases 4–5 and Milestones 4–6*
- *`unknown_items.md` — U-04, U-05 resolutions land here*
- *`script_pattern.md` — will need new entries for `trace.py`, `auth.py`, and the `backend/` package after each step ships*
