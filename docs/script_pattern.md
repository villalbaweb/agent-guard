# AgentGuard: Core Scripts and Agentic Design Patterns

This document describes each module in the `agentguard/` package — its function, the agentic pattern it implements, and the current implementation status.

---

## 1. Foundation & State

### [state.py](file:///d:/Git/agent-guard/agentguard/state.py)
- **Function:** Defines the two core TypedDicts shared across the entire LangGraph: `AgentGuardState` and `GovernanceDecision`.
- **Agentic Pattern:** **Stateful Agent Mesh** — all nodes (Orchestrator, Policy, Executor) share a single source of truth through the graph state.
- **Key fields:**
  - `AgentGuardState`: tracks task, depth, root/parent IDs, results, usage stats, budget config, and governance decisions.
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
- **Function:** Shared epistemic memory for all runs — thought history, loop detection, and blueprint caching.
- **Agentic Pattern:** **Shared Epistemic Memory** — all agents in a run share a persistent store that outlives any single graph node.
- **Backend:** Real Redis (`REDIS_URL`) with silent in-memory dict fallback for development without Docker.
- **Loop detection — semantic path:** Calls `get_embeddings()` → computes cosine similarity in pure Python (no numpy dependency on host) → blocks if similarity ≥ threshold against the last N thoughts.
- **Loop detection — exact-match fallback:** Used when embeddings are unavailable; compares `action` + `intent` strings directly.
- **Configurable:** `detect_loop(similarity_threshold, lookback_window)` — values driven from `policy.yaml` via `PolicyEngine`.
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
- **Status:** ✅ Implemented

---

## 4. Execution & Orchestration

### [executor.py](file:///d:/Git/agent-guard/agentguard/executor.py)
- **Function:** Builds and runs the LangGraph `StateGraph`. Orchestrates the full lifecycle from preflight to synthesis.
- **Agentic Pattern:** **Orchestrator & Recursive Subgraph** — enforces a deterministic Preflight → Decompose → Plan → Execute → Synthesize pipeline.
- **Graph nodes:** `preflight`, `decompose`, `plan`, `execute_subtasks`, `synthesize`, `reject`
- **Clean intent construction:** Each step check passes `"{AgentRole} performing: {subtask}"` as the intent — not the raw task string. This prevents the policy LLM from mis-classifying internal task names as policy violations.
- **Cost tracking:** Costs accumulate in `usage_stats.total_cost` only for allowed steps.
- **Recursive execution:** Child `StateGraph` instances are spawned at `depth < max_depth - 1`, sharing the parent's `root_task_id` and budget.
- **HITL signalling:** Blocked steps with `hitl_required=True` produce `HITL_PENDING` output, preserving the distinction between a hard block and a human-approval request.
- **Status:** ✅ Implemented

---

### [poc.py](file:///d:/Git/agent-guard/agentguard/poc.py)
- **Function:** End-to-end integration harness validating all three wedge use cases across five governance scenarios.
- **Agentic Pattern:** **MAS (Multi-Agent System) Orchestration** — demonstrates all patterns (Policy, Registry, Memory, Executor) working together.
- **Scenarios:**
  1. **Normal research task** — LLM decomposition, recursive execution, semantic loop detection firing in child graph
  2. **Forbidden topic** — keyword rule blocks at preflight in <1ms, zero API calls
  3. **PII detection** — SSN regex blocks at preflight in <1ms, zero API calls
  4. **Budget cap** — per-step cost ($0.05) exceeds cap ($0.04), all subtasks blocked after decomposition
  5. **Semantic loop detection (direct)** — same intent submitted three times; blocked on attempt 2 via cosine similarity
- **Embeddings status:** Reported at startup — shows whether semantic or exact-match loop detection is active.
- **Status:** ✅ Implemented
