# AgentGuard: Unknown Items & Architectural Questions

## Traceability & Input Needed

| ID | Area | Status | Description | Resolution / Next Step |
| :--- | :--- | :--- | :--- | :--- |
| **U-01** | **Shared Memory Consistency** | ⚠️ Partial | How to handle concurrent writes to Redis from parallel agents? | **Implemented:** `MemoryManager` uses Redis with silent in-memory fallback. Concurrent write safety (Redlock) not yet implemented — safe for the current serial executor but required before switching to parallel `Send`. **Next:** Integrate `Redlock` in `MemoryManager` when LangGraph `Send` is adopted. |
| **U-02** | **Policy Latency** | ✅ Resolved | What is the total latency of in-line policy checks per step? | **Resolved via cloud embeddings:** Local sentence-transformers Docker (~4 GB) was evaluated and rejected. Cloud embedding APIs (Google `gemini-embedding-001`, OpenAI `text-embedding-3-small`) add ~200–400ms per step — acceptable for current use cases. Sidecar architecture remains an option for sub-100ms requirements at scale. |
| **U-03** | **MCP Integration** | ⏳ Open | How will the Model Context Protocol integrate with the `PolicyGuard` node? MCP is the emerging standard for agentic tool calls; customers whose agents are entirely MCP-based currently have **no AgentGuard interception point** — governance is blind to those tool calls. | **Strategy:** Implement `PolicyGuard` as an MCP-aware proxy middleware. Each MCP tool call passes through `check_step()` before execution. No implementation yet — blocked on selecting MCP server framework. **Integration impact:** This is a blocker for non-LangGraph customers. See also U-13 (SDK packaging) and U-15 (state construction friction). |
| **U-04** | **Human-in-the-Loop (HITL)** | ✅ Resolved | How to implement asynchronous "pause and approve" in the recursive graph? | **Resolved (Step C):** LangGraph `interrupt()` fires when a `require_hitl` rule matches in `check_step()`. Graph state is checkpointed to Redis via `langgraph-checkpoint-redis`. `POST /runs/{id}/approve` (with `approver` JWT role) calls `graph.invoke(command=Command(resume=...))` to resume. Approval payload (`approved_by`, `jti`) logged in trace events. Propagated through nested subgraphs via `parent_trace_event_id`. |
| **U-05** | **Identity Mapping** | ✅ Resolved | How to propagate the "root user" identity through all worker subgraphs? | **Resolved (Step C):** `agentguard/auth.py` implements `AuthContext` dataclass + `create_token`/`decode_token` (HS256). `auth_context` field added to `AgentGuardState`; verbatim-copied into every child graph state. Auth subject embedded in every `GovernanceDecision.reason` as `[subject:X]`. `_auth_expired()` checked at preflight and per-step — expired tokens produce a hard block before any LLM call. |
| **U-06** | **Cost Attribution** | ⏳ Open | How to attribute costs of cached results from the `BlueprintManager`? | **Still open.** Blueprint cache is implemented in `MemoryManager` but cost attribution for cached vs. freshly computed results is undefined. **Proposed:** Tag each blueprint with its generation cost; amortize across consumers. |
| **U-07** | **Governance Resilience** | ⏳ Open | No circuit breakers defined for the governance layer itself. | **Partially mitigated:** LLM semantic guard fails closed (blocks on exception). Redis unavailability degrades to in-memory fallback — governance continues. **Remaining:** Define graduated failsafe levels: (1) rules-only mode if LLM guard fails, (2) full block if both LLM and rules fail. |

---

## New Items Identified During PoC

| ID | Area | Description | Proposed Next Step |
| :--- | :--- | :--- | :--- |
| **U-08** | **Embedding Cache** | Every `check_step()` call embeds the current thought, including near-identical repeated calls. No caching of embedding vectors. | ✅ Resolved — Implemented a Redis-backed embedding cache to deduplicate and store identical text embeddings. Significant cost/latency reduction for recursive runs. |
| **U-09** | **Registry Vector Search** | `Registry.search_by_intent()` uses substring matching. Intent routing accuracy degrades as the number of registered agents grows. | ✅ Resolved — Implemented via pgvector cosine similarity. `search_by_intent()` embeds the intent text and queries `agent_registry` using `vector <=> operator` with HNSW index. Falls back to substring matching when embeddings are unavailable. |
| **U-10** | **Parallel Execution Safety** | `RecursiveExecutor` uses a serial `for` loop over subtasks. True parallelism with LangGraph `Send` is blocked by U-01 (no Redlock yet). | ✅ Resolved — Implemented via LangGraph `Send` API. `_edge_fan_out` dispatches one `Send` per subtask; `_node_worker` executes independently. Reducers in `AgentGuardState` handle merge. |

---

## Items Identified During Real-World Implementation (asset_management.py)

| ID | Area | Status | Description | Resolution / Next Step |
| :--- | :--- | :--- | :--- | :--- |
| **U-11** | **Multi-Tenant Policy Scoping** | ✅ Resolved | `PolicyEngine` now supports dynamic policy loading via `policy_id`. API consumers can specify which policy to apply per-run. Isolation verified with tests. | **Implemented:** Resolves policy dynamically from `policies/{policy_id}.yaml`. Supports caching and path traversal protection. Backward compatible with global `POLICY_FILE`. |
| **U-12** | **Agent Registry — Static vs Dynamic** | ✅ Resolved | The `Registry` is populated at server startup in `dependencies.py::get_registry()` — agents are hardcoded, not self-registering. In the Meridian demo, the 5-agent system was wired manually. A production marketplace needs agents to register at runtime (on pod startup, via REST call, or via SDK). `search_by_intent()` also uses substring matching, which degrades as the registry grows. **Integration impact:** Any change in customer agent lineup requires a code change + server redeploy. The `POST /runs` REST API is therefore not self-service — it only works with the two generic agents hardcoded in `dependencies.py`. | **Implemented via `POST /agents/register` REST endpoint and PostgreSQL-backed `Registry`. Agents self-register at runtime; no code change or server redeploy required. Health monitoring updates `health_status` via background task.** |

---

## Learning Backlog — Production-Grade Concepts to Explore

> These items were surfaced by examining what production-grade agentic systems require. Each one is a distinct learning target: understanding the engineering mechanics illuminates how real-world agent safety, identity, and distribution challenges are solved.

| ID | Area | Status | Description | Resolution / Next Step |
| :--- | :--- | :--- | :--- | :--- |
| **U-13** | **SDK Packaging — No PyPI Distribution** | ⏳ Open | There is no installable package. Exploring this teaches how a reusable agent-safety layer is packaged and distributed — versioning, build metadata, changelog discipline, and the local `pip -e` / `uv` install workflow. | Flesh out `pyproject.toml` `[project]` table and entry points; add a `CHANGELOG.md`. No PyPI publish required for a playground; a local `uv`/`pip -e` install is the goal. |
| **U-14** | **Async Run Completion — No Webhooks** | ⏳ Open | `POST /runs` returns `run_id` immediately and the graph executes in the background. The only way to detect completion is polling. Implementing webhooks here teaches the classic long-running-job pattern and async notification design. | Add an optional `callback_url` field to `RunCreateRequest`. On run completion/block/error, POST the `RunStatusResponse` payload to the webhook URL via an async background task (`asyncio` or a worker queue). |
| **U-15** | **`AgentGuardState` Construction Friction** | ✅ Resolved | `AgentGuardState` is a 14-field `TypedDict` with several non-obvious fields (`parent_trace_event_id`, `_current_edge`, `auth_context` dict format). Customers building from the SDK must hand-construct the entire struct. There is no factory function, builder, or sensible defaults — a single missing field causes a silent runtime failure or LangGraph type error. The `asset_management.py` demo required 20+ lines just to initialize state. | Implemented `make_initial_state()` factory in `state.py`. Accepted fields are now limited to user-facing inputs (`task`, `subject`, `budget_config`) while system fields are auto-populated with correct defaults. Reduces integration code by 80%. |
| **U-16** | **CORS Wildcard in Production** | ⏳ Open | `backend/main.py` defaults `allow_origins` to `"*"`. Implementing a startup warning here teaches how misconfiguration detection is wired into a server process — a broadly applicable pattern for any security-sensitive env var. | Add a startup log warning when `ALLOWED_ORIGINS` is not set or equals `"*"`. Document in `.env.example` with a non-wildcard example value. |
| **U-17** | **RS256 / JWKS JWT — Production Identity** | ⏳ Open | `agentguard/auth.py` uses HS256 with a shared secret. Exploring RS256/JWKS here teaches how OIDC/OAuth identity federation meets agent security contexts — a fundamental pattern when multiple services need to validate tokens without sharing a secret. | Implement RS256 token validation in `decode_token()`. Add `AGENTGUARD_JWKS_URL` env var. On validation, fetch the public key from the JWKS endpoint (with caching) and verify the signature. HS256 path stays for dev/self-hosted. |
| **U-18** | **Policy UI — Visual Rule Editing** | ⏳ Open | `policy.yaml` requires direct file access and YAML knowledge to edit. Building a UI here teaches the pedagogical core of this playground: edit a rule, reload, re-run the experiment, watch the trace change — making governance tangible. | For MVP: add a Policy tab in the React frontend wired to `GET /policy` + `POST /policy/reload`. Add a simple web form for common rule types (budget cap, blocked keywords, HITL triggers). Policy change events emitted as trace events for auditability. |

---

*Referenced Sections:*
- *Implementation Plan: Phases 1–5*
- *TSD: Section 6 (State Management), Section 11 (Registry Pattern), Section 12 (Policy Scoping)*
- *TSD: Section 8 (Security & Safety)*
- *Modernization Doc: Section 8 (Remaining Gaps)*
- *Production-Grade Concepts Analysis: April 2026*
