# AgentGuard: Implementation Plan (MVP)

## Goals and Non-Goals
- **Goal:** Unify the recursive, parallel execution of SSA (Project B) with the governance and policy engine of ACP (Project A).
- **Goal:** Reach an MVP that can handle multi-step, complex tasks (e.g., "Analyze the security of X and suggest fixes") while enforcing safety and cost constraints.
- **Non-Goal:** Full UI development.
- **Non-Goal:** Multi-cloud/multi-region deployment beyond local/single-cluster.

## System Overview
AgentGuard uses a **Supervisor-Worker-Guard** architecture:
1. **Orchestrator (SSA):** Decomposes tasks via LLM, creates subtasks, and manages recursive worker subgraphs.
2. **Control Plane / Guard (ACP):** Intercepts every step to evaluate YAML-defined policies, check for semantic loops, and enforce budget limits.

## Agentic Architecture
Implements the **Fractal Chain-of-Thought (FCoT)** and **Supervisor** patterns.

### Key Components
- **Global Orchestrator:** Primary entry point; uses LLM-backed `TaskDecomposer` and intent-routing `ExecutionPlanner`.
- **Governance Node (Guard):** Shared policy check before every executor step. Uses `PolicyEngine` (YAML rules + LLM semantic guard) and `MemoryManager` (loop detection).
- **Recursive Executor:** LangGraph `StateGraph` that spawns child graphs for parallel task decomposition at configurable max depth.
- **Telemetry & Traceability:** Governance decisions recorded in `AgentGuardState.governance_decisions` per run. Full causal graph export pending (Phase 4).
- **Shared Epistemic Memory:** Redis-backed `MemoryManager` for thought history, embeddings-based loop detection, and blueprint caching.

---

## Implementation Status

### ✅ Phase 0: Governance Stress Test & Latency PoC
- **Objective:** Measure governance tax and validate distributed system stability under recursive load.
- **Completed:** 5-scenario PoC (`poc.py`) validates all governance paths. Latency baseline established:
  - Keyword/regex rules: <1ms (zero API calls)
  - Semantic loop detection (embeddings): ~200–400ms per step (cloud API round-trip)
  - Full recursive run (3 LLM subtasks): ~20–55s (dominated by Gemini API latency)

### ✅ Phase 1: Unified State, Registry & Shared Memory
- `AgentGuardState` and `GovernanceDecision` unified with `hitl_required` field.
- `MemoryManager` backed by real Redis with in-memory fallback.
- Semantic loop detection via cosine similarity on cloud embeddings (Google `gemini-embedding-001` / OpenAI `text-embedding-3-small`).
- Blueprint cache wired to Redis.
- **Remaining:** Registry still in-memory — Redis + vector similarity search pending.

### ✅ Phase 2: Governance & Intent Routing
- `PolicyEngine` loads `policy.yaml` at init; rules compiled at startup.
- Content rules support `regex` and `contains` operators with `block`, `allow`, `require_hitl` actions.
- Budget enforcement: per-step ceiling + cumulative cap; state-level `budget_config` overrides policy defaults.
- Loop detection delegated to `MemoryManager` with configurable threshold and lookback window.
- LLM semantic guard with structured prompt distinguishing internal task execution from genuine violations.
- `ExecutionPlanner.route_intent()` uses registry keyword search; full vector-similarity routing pending.

### ✅ Phase 3: Recursive Execution
- `RecursiveExecutor` builds a `StateGraph` with clean Preflight → Decompose → Plan → Execute → Synthesize lifecycle.
- `decompose()` uses real LLM with JSON-output prompt; produces clean subtask names (no raw task string leakage).
- Clean `action`/`intent` strings passed to `check_step()` to prevent policy false-positives.
- Child graphs spawned at `depth < max_depth - 1`; share parent `root_task_id` and budget.
- Cost tracking accumulates only for allowed steps.

### 🔄 Phase 4: Telemetry & Observability (Partial)
- Governance decisions recorded per-step in `AgentGuardState.governance_decisions`.
- Structured log output with `[ALLOW]` / `[BLOCK]` / `[HITL]` tags.
- **Remaining:** Machine-readable JSON causal dependency graph artifact per run. Jaeger OTLP tracing integration (defined in `docker-compose.local.yml`, not yet wired).

### ⏳ Phase 5: Validation & Benchmarking (Partial)
- PoC validated against 5 governance scenarios covering all wedge use cases.
- **Remaining:** Adversarial prompt injection testing, load testing under concurrent recursive runs, latency regression suite.

---

## LangGraph Graph Design

1. **Entry Node:** `preflight` — `PolicyEngine.check_preflight()` blocks forbidden tasks before any LLM calls
2. **Decompose Node:** `decompose` — LLM breaks task into clean, focused subtask strings
3. **Plan Node:** `plan` — intent extraction routes each subtask to the best registered agent
4. **Execute Node:** `execute_subtasks` — per-step policy check → cost accumulation → recursive child graph
5. **Synthesize Node:** `synthesize` — joins subtask outputs into `final_answer`
6. **Reject Node:** `reject` — terminal node for preflight failures

---

## Next Milestones

### Milestone 4: Causal Graph & Audit Trail (Phase 4)
- Export per-run JSON trace with full decision chain
- Wire Jaeger OTLP for real-time distributed tracing

### Milestone 5: REST API & Multi-tenancy
- FastAPI backend from `docker-compose.local.yml`
- Per-tenant policy scoping and JWT identity propagation (resolves U-05)
- Python SDK as thin wrapper over REST API

### Milestone 6: Parallel Execution
- Replace serial `for` loop in `RecursiveExecutor` with LangGraph `Send`
- Redis-backed Registry with vector similarity search

---

## Risks and Mitigations

| Risk | Mitigation |
| :--- | :--- |
| High latency from cloud embedding calls per step | Cache embeddings by content hash in Redis; only embed novel thoughts |
| Recursive depth leading to state bloat | `Summarizer` node planned at every recursion level; hard max_depth=3 enforced |
| LLM semantic guard false-positives | Structured prompt with explicit ALLOW bias for internal operations; confirmed in PoC testing |
| Redis unavailability in production | In-memory fallback active; circuit breaker for governance layer planned (U-07) |

---

*Patterns Referenced:*
- *Supervisor Architecture [NotebookLM]*
- *Fractal Chain-of-Thought (FCoT) [NotebookLM]*
- *Real-Time Compliance Monitoring [NotebookLM]*
- *Shared Epistemic Memory [NotebookLM]*
