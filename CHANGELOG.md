# Changelog

All notable changes to AgentGuard are documented here.

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
