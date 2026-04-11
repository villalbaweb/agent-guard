# AgentGuard: Core Scripts and Agentic Design Patterns

This document describes each module in the `agentguard/` package — its function, the agentic pattern it implements, and the current implementation status.

---

## 1. Foundation & State

### [state.py](file:///d:/Git/agent-guard/agentguard/state.py)
- **Function:** Defines the two core TypedDicts shared across the entire LangGraph: `AgentGuardState` and `GovernanceDecision`.
- **Agentic Pattern:** **Stateful Agent Mesh** — all nodes (Orchestrator, Policy, Executor) share a single source of truth through the graph state.
- **Key fields:**
  - `AgentGuardState`: tracks task, depth, root/parent IDs, results, usage stats, budget config, governance decisions, **plus** three new fields added in Steps A/C:
    - `trace_events: List[Dict]` — append-only causal event log (one entry per node boundary and `check_step` call)
    - `auth_context: Dict` — JWT-derived caller identity (subject, tenant, roles, expiry)
    - `parent_trace_event_id: Optional[str]` — causal link from the parent graph's `execute_subtasks` event
  - `GovernanceDecision`: records every policy evaluation with `action`, `allowed`, `reason`, `cost`, and `hitl_required` — the last field distinguishes a hard block from a human-approval pause.
- **Status:** ✅ Implemented

---

### [llm.py](file:///d:/Git/agent-guard/agentguard/llm.py)
- **Function:** Two factory functions consumed across the package:
  - `get_llm()` — returns an initialized `BaseChatModel` or `None` (mock mode)
  - `get_embeddings()` — returns a callable `(list[str]) -> list[list[float]]` for semantic similarity, or `None`
- **Agentic Pattern:** **Model-Agnostic Interfacing** — the entire system swaps its "brain" via environment variables with no code changes.
- **Provider resolution (LLM):** Google AI Studio → Anthropic → OpenAI-compatible → Vertex AI
- **Provider resolution (Embeddings):** OpenAI → Google AI Studio → `None` (exact-match fallback)
- **OpenAI-compatible note:** `OPENAI_BASE_URL` redirects to OpenRouter, LiteLLM, vLLM, Ollama, or any compatible endpoint.
- **Status:** ✅ Implemented

---

## 2. Infrastructure & Discovery

### [memory.py](file:///d:/Git/agent-guard/agentguard/memory.py)
- **Function:** Shared epistemic memory for all runs — thought history, loop detection, blueprint caching, and run state persistence for the REST API.
- **Agentic Pattern:** **Shared Epistemic Memory** — all agents in a run share a persistent store that outlives any single graph node.
- **Backend:** Real Redis (`REDIS_URL`) with silent in-memory dict fallback for development without Docker.
- **Loop detection — semantic path:** Calls `get_embeddings()` → computes cosine similarity in pure Python (no numpy dependency on host) → blocks if similarity ≥ threshold against the last N thoughts.
- **Loop detection — exact-match fallback:** Used when embeddings are unavailable; compares `action` + `intent` strings directly.
- **Configurable:** `detect_loop(similarity_threshold, lookback_window)` — values driven from `policy.yaml` via `PolicyEngine`.
- **REST API helpers (Step B):** `store_run(run_id, state)` / `get_run(run_id)` — persist full `AgentGuardState`; `store_trace(run_id, trace_doc)` / `get_trace(run_id)` — persist trace JSON under `run:{id}:trace`.
- **Status:** ✅ Implemented

---

### [registry.py](file:///d:/Git/agent-guard/agentguard/registry.py)
- **Function:** Central "Yellow Pages" where agents and tools register their metadata (role, semantic description, input/output schemas).
- **Agentic Pattern:** **Capability Registry** — enforces intent-based discovery over hard-coded function calls.
- **Current backend:** In-memory dict with substring-match `search_by_intent()`.
- **Next step:** Redis-backed storage + vector similarity search to replace substring matching.
- **Status:** ⚠️ Partially implemented (in-memory, no vector search yet)

---

## 3. Intelligence & Control

### [orchestrator.py](file:///d:/Git/agent-guard/agentguard/orchestrator.py)
- **Function:** Two-stage planner: `decompose()` breaks the task into focused subtasks via LLM; `plan()` routes each subtask to the best registered agent via intent extraction.
- **Agentic Pattern:** **Strategic Planner** — separates "Thinking" (decomposition/planning) from "Doing" (execution).
- **`decompose()`:** Calls the LLM with a structured JSON-output prompt. Returns clean, short subtask descriptions that do not carry the parent task string — prevents false positives in downstream policy checks. Falls back to two generic subtasks if the LLM call fails.
- **`plan()`:** Extracts the primary intent verb from each subtask via LLM, constrains output to a single word, then looks up matching agents in the registry.
- **Status:** ✅ Implemented (LLM-backed, no longer mocked)

---

### [policy.py](file:///d:/Git/agent-guard/agentguard/policy.py)
- **Function:** The inline governance control plane. Evaluates every preflight check and per-step action against `policy.yaml` rules before execution proceeds.
- **Agentic Pattern:** **Watchdog / Semantic Guardrail (Zero-Trust Execution)** — no agent action runs without an explicit ALLOW decision.
- **Rule loading:** Loads `policy.yaml` at init (path from `POLICY_FILE` env var). Deep-merges with built-in defaults so the system is safe even without a policy file.
- **Evaluation order in `check_step()`:**
  1. Per-step cost ceiling (from `policy.yaml` or state override)
  2. Cumulative budget check
  3. Content rules — compiled `regex` and `contains` operators, first match wins
  4. LLM semantic guard — structured prompt that distinguishes internal task execution from genuine violations
  5. Loop detection — delegates to `MemoryManager.detect_loop()` with threshold/window from policy
- **Rule actions:** `block` (hard deny), `allow` (explicit pass), `require_hitl` (sets `hitl_required=True` in `GovernanceDecision` instead of blocking).
- **Budget override:** State-level `budget_config` takes precedence over `policy.yaml` for per-run limits.
- **Auth subject in audit (Step C):** `_auth_subject(state)` extracts caller identity from `auth_context`; every `GovernanceDecision.reason` is prefixed with `[subject:X]` for a complete, attributable audit trail.
- **Status:** ✅ Implemented

---

## 4. Execution & Orchestration

### [executor.py](file:///d:/Git/agent-guard/agentguard/executor.py)
- **Function:** Builds and runs the LangGraph `StateGraph`. Orchestrates the full lifecycle from preflight to synthesis.
- **Agentic Pattern:** **Orchestrator & Recursive Subgraph** — enforces a deterministic Preflight → Decompose → Plan → Execute → Synthesize pipeline.
- **Graph nodes:** `preflight`, `decompose`, `plan`, `execute_subtasks`, `synthesize`, `reject`
- **Clean intent construction:** Each step check passes `"{AgentRole} performing: {subtask}"` as the intent — not the raw task string. This prevents the policy LLM from mis-classifying internal task names as policy violations.
- **Cost tracking:** Costs accumulate in `usage_stats.total_cost` only for allowed steps.
- **Recursive execution:** Child `StateGraph` instances are spawned at `depth < max_depth - 1`, sharing the parent's `root_task_id` and budget. Child `auth_context` is copied verbatim from the parent state.
- **Trace emission (Step A):** Every node boundary calls `trace.new_event()` with the previous event's `event_id` as `parent_event_id`. Child graph trace events are merged into the parent via `trace_events.extend(child["trace_events"])`.
- **Auth expiry checks (Step C):** `_auth_expired()` is evaluated at preflight and before each per-step edge; expired contexts produce an immediate REJECT / blocked event.
- **HITL wiring (Step C):** When `check_step()` returns `hitl_required=True`, `executor` calls LangGraph `interrupt({event_id, subtask, reason, required_role})`, pausing the graph for external approval. Graph state is checkpointed to Redis for durability.
- **Status:** ✅ Implemented

---

### [poc.py](file:///d:/Git/agent-guard/agentguard/poc.py)
- **Function:** End-to-end integration harness validating all three wedge use cases across five governance scenarios.
- **Agentic Pattern:** **MAS (Multi-Agent System) Orchestration** — demonstrates all patterns (Policy, Registry, Memory, Executor, Trace) working together.
- **Scenarios:**
  1. **Normal research task** — LLM decomposition, recursive execution, semantic loop detection firing in child graph
  2. **Forbidden topic** — keyword rule blocks at preflight in <1ms, zero API calls
  3. **PII detection** — SSN regex blocks at preflight in <1ms, zero API calls
  4. **Budget cap** — per-step cost ($0.05) exceeds cap ($0.04), all subtasks blocked after decomposition
  5. **Semantic loop detection (direct)** — same intent submitted three times; blocked on attempt 2 via cosine similarity
- **Trace output (Step A):** Each scenario writes a `./traces/{run_id}.json` file conforming to the `schema_version: "1.0"` trace schema.
- **Embeddings status:** Reported at startup — shows whether semantic or exact-match loop detection is active.
- **Status:** ✅ Implemented

---

## 5. Observability

### [trace.py](file:///d:/Git/agent-guard/agentguard/trace.py)
- **Function:** Causal dependency graph builder. Produces a machine-readable JSON artifact per run capturing the full reasoning chain for audit and compliance.
- **Agentic Pattern:** **Causal Dependency Graph** — append-only event log with parent-child links that form a tree suitable for graph visualization and SIEM ingestion.
- **`new_event(run_id, node, depth, parent_event_id?, ...)`:** Factory returning a plain JSON-serializable dict with fields `event_id` (uuid4), `parent_event_id`, `node`, `depth`, `timestamp`, `agent_id`, `action`, `intent`, `decision` (inlined `GovernanceDecision`), `cost_delta`, `cumulative_cost`, `status`, and `metadata`.
- **`dump(state, ...)`:** Serializes `state.trace_events` into the canonical schema (`schema_version: "1.0"`) with `events` list and `edges` adjacency list. Computes `total_duration_ms`. Accepts an optional `redactor` callable for PII scrubbing before persistence.
- **`write_to_disk(trace, directory)`:** Writes `./traces/{run_id}.json` (creates directory if absent).
- **Storage:** Local disk by default; REST API (`RunStore`) also persists traces to Redis under `run:{id}:trace` for multi-instance access.
- **Status:** ✅ Implemented

---

## 6. Security & Identity

### [auth.py](file:///d:/Git/agent-guard/agentguard/auth.py)
- **Function:** JWT generation, verification, and the `AuthContext` dataclass that carries caller identity through the entire execution graph.
- **Agentic Pattern:** **Governance Identity Propagation** — every request to the system carries a verifiable, immutable identity that is threaded from the root graph through all child subgraphs.
- **`AuthContext`:** Dataclass with `subject`, `tenant_id`, `roles`, `issued_at`, `expires_at`, `token_id` (jti). `is_expired()` and `has_role(role)` helpers.
- **`create_token(ctx)`:** Encodes `AuthContext` into a signed HS256 JWT. Secret from `AGENTGUARD_JWT_SECRET` env var.
- **`decode_token(token)`:** Verifies signature and issuer (`AGENTGUARD_JWT_ISSUER`), returns `AuthContext`.
- **`make_auth_context(subject, roles, ...)`:** Convenience factory for dev/testing.
- **`anonymous_context()`:** Returns a non-expiring context for use when `AGENTGUARD_NO_AUTH=1`.
- **Production note:** HS256 for dev. RS256 with JWKS endpoint is deferred to Phase 6.
- **Status:** ✅ Implemented

---

## 7. REST API Layer (`backend/`)

### [backend/main.py](file:///d:/Git/agent-guard/backend/main.py)
- **Function:** FastAPI application entry point — wires CORS middleware, global error handler, and all route prefixes.
- **Key detail:** All orchestration logic stays in `agentguard/`; `backend/` is a thin HTTP facade.
- **Status:** ✅ Implemented

### [backend/schemas.py](file:///d:/Git/agent-guard/backend/schemas.py)
- **Function:** Pydantic v2 request/response models for all endpoints.
- **Key models:** `RunCreateRequest`, `RunCreateResponse`, `RunStatusResponse`, `ApproveRequest`, `ApproveResponse`, `PolicyResponse`, `PolicyReloadResponse`, `HealthResponse`, `ErrorResponse`.
- **Status:** ✅ Implemented

### [backend/store.py](file:///d:/Git/agent-guard/backend/store.py)
- **Function:** `RunStore` facade managing run lifecycle state (status, final answer, cost summary, trace URL, HITL pending steps) in Redis via `MemoryManager`.
- **Key methods:** `create_run`, `set_status`, `get_status`, `save_state`, `save_trace`, `load_trace`.
- **Status:** ✅ Implemented

### [backend/dependencies.py](file:///d:/Git/agent-guard/backend/dependencies.py)
- **Function:** FastAPI dependency injection — provides `get_executor`, `get_run_store`, `require_auth` (JWT bearer or API key or no-auth mode), `require_approver` (role check).
- **`require_auth`:** Validates `Authorization: Bearer <token>` or `X-API-Key` header; sets `AuthContext` for the request. Bypassed when `AGENTGUARD_NO_AUTH=1`.
- **`require_approver`:** Extends `require_auth`; additionally enforces `roles` contains `"approver"` — 403 otherwise.
- **Status:** ✅ Implemented

### [backend/routes/runs.py](file:///d:/Git/agent-guard/backend/routes/runs.py)
- **Function:** Run lifecycle endpoints.
- **Endpoints:** `POST /runs` (submit, background execution), `GET /runs/{id}` (status + answer), `GET /runs/{id}/trace` (trace JSON), `POST /runs/{id}/approve` (HITL resume).
- **HITL resume:** Calls `graph.invoke(None, config, command=Command(resume=approval_payload))` on the existing checkpointed graph thread.
- **Status:** ✅ Implemented

### [backend/routes/policy.py](file:///d:/Git/agent-guard/backend/routes/policy.py)
- **Function:** Policy inspection and hot-reload.
- **Endpoints:** `GET /policy` (return loaded rules), `POST /policy/reload` (reload `policy.yaml` from disk; 409 if already in progress).
- **Status:** ✅ Implemented

### [backend/routes/health.py](file:///d:/Git/agent-guard/backend/routes/health.py)
- **Function:** Liveness probe returning Redis and LLM availability.
- **Status:** ✅ Implemented
