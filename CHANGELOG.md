# Changelog

All notable changes to AgentGuard are documented here.

## [Unreleased]

### Fixed
- **HITL interrupt was silently swallowed** — `interrupt()` signals a pause by raising `GraphInterrupt` (an `Exception` subclass), which the worker's generic `except` converted into an inline `HITL_PENDING` result, so the graph never checkpointed even with a checkpointer configured. `GraphInterrupt` now propagates; the inline fallback remains for checkpointer-less runs.
- **HITL resume never delivered the approval** — `POST /runs/{id}/approve` called `graph.invoke(None, config, command=Command(...))`; `command` is not an `invoke` parameter and was silently ignored. Now invokes `graph.invoke(Command(resume=...), config)`, and resumes a *specific* step by interrupt id so runs with multiple parallel paused workers can be approved one at a time.
- **Checkpointer rebuilt per request** — every request created a fresh `MemorySaver`, so the checkpoint written by `POST /runs` was invisible to `POST /approve`. The checkpointer is now a process-wide singleton (`get_checkpointer`), and the Redis variant handles `from_conn_string` returning a context manager.
- **Interrupted runs were reported "completed"** — `_execute_run` ignored `__interrupt__` in the returned state (and the non-serializable `Interrupt` objects would have crashed persistence). Runs now report `hitl_pending` with the interrupt payloads as `pending_steps`.
- **Duplicated governance decisions** — the planner's `decompose`/`plan` nodes returned the full mutated state, so `operator.add` reducers re-appended every accumulated `governance_decisions` entry on each node. Planner nodes now return partial state updates.
- **Reflection replan was unreachable** — clearing `results`/`all_edges` with empty values is a no-op under merge/add reducers, so the F-01 replan branch could never fire (and would have duplicated edges if it had). Reflect now blanks `final_answer` explicitly and `all_edges` uses a last-write-wins reducer so each planning pass replaces stale edges.
- **`subtasks` leaked into the final answer** — synthesize only skipped `final_answer`/`subtasks_output`, so the raw subtask list decompose stores under `results["subtasks"]` was concatenated into every answer.
- **Decompose trace event recorded no subtasks** — metadata read subtasks from `all_edges` (always empty at decompose time) instead of the decomposition result.
- **Zero-value budget overrides ignored** — `budget_config={"max_cost_usd": 0.0}` fell through to the policy default due to `or`-based fallback; now uses explicit `None` checks.

### Fixed (Docker)
- **Gemini embeddings never stored** — `gemini-embedding-001` returns 3072-dim vectors while the pgvector schema expects `EMBEDDING_DIM` (1536), so every agent was silently stored without an embedding and all routing fell back to `fallback_agent`. The Google embedder now passes `output_dimensionality=EMBEDDING_DIM` (MRL truncation).
- **Embedding cache collisions** — Redis cache keys now include provider/model/dimension so switching embedders can't return stale vectors of the wrong size.
- **`policies/` missing from the backend image** — per-tenant `policy_id` runs failed inside Docker; `Dockerfile.backend` now copies the directory.
- **Checkpointer fallback** — a Redis-saver failure now degrades to `MemorySaver` instead of disabling HITL entirely.

### Changed (Docker)
- `docker-compose.yml`: Redis image switched to `redis/redis-stack-server` (RediSearch is required by `langgraph-checkpoint-redis`); removed obsolete `version:` key.
- `Dockerfile.backend`: installs `langgraph-checkpoint-redis` so HITL checkpoints survive backend restarts.

### Changed
- **Shared `PolicyEngine` singleton** — executors (including recursive child graphs) and the `/policy` routes now share one engine, so `POST /policy/reload` actually affects subsequent runs and circuit-breaker state persists across requests.
- `POST /runs/{id}/approve` returns 500 (instead of a misleading `resumed`) when the resume fails.
- `/policy/reload` guard uses a real `threading.Lock` instead of a racy boolean.
- `MCPSafetyProxy` sync/async paths share one policy-check implementation.
- Live-PostgreSQL tests skip (instead of fail) when `DATABASE_URL` is set but the server is unreachable.
- Frontend: node status colors extracted to a shared helper; fixed invalid `border-yellow-250` classes and an invisible close button in light mode; removed `window.cy` debug globals.

## [0.2.0] — 2026-06-07

### Added
- **Evaluation harness** (`agentguard/eval.py`, `scripts/run_eval.py`) — runs scenarios with governance ON vs OFF and reports whether policy changed behavior, cost, or safety outcomes (F-04).
- **MCP Safety Middleware** (`agentguard/mcp_proxy.py`) — `MCPSafetyProxy` intercepts MCP tool calls through `PolicyEngine.check_step()` before execution, closing the governance gap for MCP-based agents (F-05).
- **Reflection / self-critique loop** — optional `reflect` node in `RecursiveExecutor` (`enable_reflection=True`). After synthesis the LLM critiques the answer; an INADEQUATE verdict triggers one re-plan cycle. All reflection decisions emit trace events (F-01).
- **Dynamic replanning on failure** — `RecursiveExecutor._node_worker` searches the registry for an alternative agent when a step is policy-blocked (F-02).
- **Distributed write lock** (`agentguard/memory.py`) — Redis SETNX-based lock around `record_thought` prevents lost-update races under parallel `Send` fan-out (D-01).
- **Governance circuit breaker** (`agentguard/policy.py`) — graduated failsafe: after 3 consecutive LLM guard failures the engine switches to rules-only mode; after 6 total failures it switches to full-block mode (D-03).
- **RS256 / JWKS identity** (`agentguard/auth.py`) — `decode_token` checks `AGENTGUARD_JWKS_URL`; if set, fetches and caches the public key and validates RS256 tokens. HS256 path kept for dev (P-01).
- **Async run webhooks** — optional `callback_url` field on `RunCreateRequest`; AgentGuard POSTs the run status payload to the webhook on completion, block, or error (D-02).
- **Trace schema 1.1** (`agentguard/trace.py`) — per-event `llm_token_counts` and `latency_breakdown` fields for cost/latency visibility.
- **Mermaid trace export** — `GET /runs/{id}/trace?format=mermaid` returns a Mermaid flowchart.
- **Policy tab** in React frontend — read-only view of active policy rules with hot-reload button.
- **CORS startup warning** — log warning when `ALLOWED_ORIGINS` defaults to `*`.
- **SDK packaging** — proper `pyproject.toml` metadata; `agentguard` is now installable via `pip install -e .`.

### Changed
- Project reframed as an **educational agentic-AI research playground** (not a commercial product).
- `docs/technical_specification_document.md` §12 reframed: "Customer Interaction Modes" → "Interaction Surfaces".
- `docs/unknown_items.md` backlog reframed from sales objectives to learning objectives.
- Root `README.md` added with "IS / IS NOT" framing.

## [0.1.0] — 2026-05-01

### Added
- Initial implementation: Supervisor-Worker-Guard pattern on LangGraph.
- `PolicyEngine` with budget, content rules, LLM semantic guard, loop detection.
- `MemoryManager` with Redis backend and in-memory fallback.
- `Registry` with pgvector semantic search and in-memory fallback.
- `AuthContext` / JWT identity propagation through recursive subgraphs.
- HITL pause/resume via LangGraph `interrupt()` + Redis checkpointer.
- REST API (`FastAPI`): runs, policy, agents, health endpoints.
- React frontend with Cytoscape causal graph visualizer.
- Parallel execution via LangGraph `Send` fan-out (U-10).
- Per-scenario policy scoping via `policy_id` (U-11).
- Dynamic agent self-registration via `POST /agents/register` (U-12).
