# Reversion to Core Spec & Technical Focus Analysis

This document provides a detailed analysis of the reversion of the **AgentGuard** project from a commercial SaaS/startup-oriented product roadmap back to its original target architecture: an **educational, research, and playground framework** for exploring advanced agentic technologies. 

The focus of this project is strictly academic, learning-oriented, and experimental. Commercial feasibility reports, marketing roadmaps, and simulated investor pitch evaluations (e.g., startup assessments) are out of scope. However, the **traceability and visualization improvements** (e.g., causal event tracing and the interactive React frontend) introduced during the productization phase are highly valuable for educational analysis, debugging, and understanding agent behavior, and **must be retained and enhanced**.

---

## 1. Directory Structure and Component Map

Below is a detailed map of the current workspace directory [agent-guard](file:///D:/Git/agent-guard) to guide the next agent through the codebase structure:

```
agent-guard/
├── .agent/                             # Agent configuration & analysis files
│   ├── apikey.png                      # API key screenshot/reference
│   ├── paul_graham_evaluator.md        # [OBSOLETE] Startup evaluator prompt
│   ├── pg_evaluation.md                # [OBSOLETE] Simulated startup assessment
│   └── revert_to_core_spec_analysis.md # [THIS FILE] Core spec alignment analysis
├── agentguard/                         # Core execution framework (Python package)
│   ├── __init__.py                     # Package initialization
│   ├── auth.py                         # Authentication & identity propagation
│   ├── db.py                           # PostgreSQL & pgvector connection pool
│   ├── executor.py                     # RecursiveExecutor (LangGraph orchestration)
│   ├── health_checker.py               # Registry agent/endpoint microservice probe
│   ├── llm.py                          # LLM & Embeddings Provider Factories
│   ├── memory.py                       # MemoryManager (Redis/In-Memory & loop detection)
│   ├── orchestrator.py                 # Main orchestration loop logic
│   ├── poc.py                          # Direct framework run & stress testing
│   ├── policy.py                       # PolicyEngine (guardrails, cost/content rules)
│   ├── registry.py                     # Dynamic Agent Registry (pgvector & memory fallback)
│   ├── state.py                        # AgentGuardState (shared TypedDict state)
│   └── trace.py                        # TraceBuilder (causal dependency JSON events)
├── backend/                            # FastAPI HTTP gateway
│   ├── main.py                         # FastAPI startup & lifespan hook
│   ├── dependencies.py                 # Dependency Injection container
│   ├── schemas.py                      # Pydantic schemas for API endpoints
│   ├── store.py                        # RunStore (wraps memory for HTTP requests)
│   └── routes/                         # REST Endpoint routers
│       ├── agents.py                   # Dynamic agent registration & retrieval
│       ├── health.py                   # System liveness probe
│       ├── policy.py                   # Policy inspection & reload
│       └── runs.py                     # Run submit, state check, and trace fetch
├── frontend/                           # React + Vite Trace Visualization Dashboard
│   ├── Dockerfile                      # Nginx production deployment container
│   ├── package.json                    # Frontend node dependencies
│   ├── tailwind.config.js              # CSS utility setup
│   └── src/                            # App code
│       ├── App.tsx                     # Main visualizer UI (CytoscapeJS graph viewer)
│       ├── types.ts                    # TypeScript interface declarations
│       └── main.tsx                    # React mounting configuration
├── docs/                               # System architecture & specification documents
│   ├── detailed_engineering.md         # Full code breakdown & sequencing guides
│   ├── implementation_plan.md          # Architecture & milestone guide
│   ├── modernization_and_llm_integration.md # Environment & factory setup
│   ├── parallel_execution_plan.md      # Concurrency & LangGraph Send planning
│   ├── script_pattern.md               # Script patterns
│   ├── technical_specification_document.md # Main architectural blueprint
│   ├── testing_guide.md                # Testing practices
│   └── unknown_items.md                # Architectural backlog
├── examples/                           # End-to-end usage examples
│   ├── asset_management.py             # Direct Python SDK execution example
│   └── policy_meridian.yaml            # Sample policy file for Meridian demo
├── policies/                           # YAML policy definitions per tenant
│   ├── tenant_a.yaml                   # Sample tenant A rules
│   └── tenant_b.yaml                   # Sample tenant B rules
├── tests/                              # Pytest suite
│   ├── test_agents_api.py              # Endpoint tests for registry API
│   ├── test_api.py                     # General API status & run submission tests
│   ├── test_auth.py                    # JWT parsing & lifecycle checks
│   ├── test_db.py                      # PostgreSQL & pgvector connection tests
│   ├── test_embed_cache.py             # Redis embedding caching verification
│   ├── test_health_checker.py          # Background health check mock tests
│   ├── test_hitl.py                    # Human-In-The-Loop interrupt/resume test
│   ├── test_parallel.py                # LangGraph Send concurrency verification
│   ├── test_policy_scoping.py          # Per-tenant yaml scoping tests
│   ├── test_registry.py                # PostgreSQL vector similarity vs ILIKE search
│   ├── test_state.py                   # State initialization validation
│   └── test_trace.py                   # Event causal logging check
├── Dockerfile.backend                  # FastAPI app container
├── docker-compose.yml                  # Full local stack compose file (Redis+Postgres+Vite)
├── docker-compose.poc.yml              # Slim local stack compose file (Redis+FastAPI)
├── policy.yaml                         # Global baseline governance rules
└── pyproject.toml                      # Package metadata & uv configuration
```

---

## 2. Traceability Assets to Keep and Leverage

The following features were created to support observability/auditing, which is crucial for educational tracing of autonomous processes:

1. **Causal Trace Logging**: 
   - [agentguard/trace.py](file:///D:/Git/agent-guard/agentguard/trace.py): Houses the [dump](file:///D:/Git/agent-guard/agentguard/trace.py#L99) function, which converts the flat array of `trace_events` accumulated in the `AgentGuardState` into a hierarchically connected JSON graph (schema version 1.0) with causal edges (`from` event A `to` event B).
2. **Causal Trace HTTP Endpoint**:
   - [backend/routes/runs.py](file:///D:/Git/agent-guard/backend/routes/runs.py): Implements `GET /runs/{id}/trace`, retrieving the complete execution trace from the [MemoryManager](file:///D:/Git/agent-guard/agentguard/memory.py) (stored in Redis or in-memory fallback) and serving it.
3. **Interactive React Visualizer**:
   - [frontend/src/App.tsx](file:///D:/Git/agent-guard/frontend/src/App.tsx): Uses `cytoscape-dagre` to lay out the trace nodes in a top-to-bottom hierarchy. It maps nodes representing graph boundaries, preflights, planning choices, and worker agents, color-coding them by execution status (`ok` = green, `blocked`/`error` = red, `hitl_pending` = yellow).
   - Allows users to inspect step-by-step LLM inputs, costs, intents, decisions, and execution duration.

---

## 3. Obsolete Productization Assets to Remove/Clean Up

The following assets are direct remnants of the commercialization pivot. They must be marked as obsolete or removed to restore clean educational focus:

1. **`.agent` Startup Reports**:
   - [paul_graham_evaluator.md](file:///D:/Git/agent-guard/.agent/paul_graham_evaluator.md) (obsolete): A system prompt guiding an LLM to play the role of Paul Graham evaluating a startup. Since the goal is purely learning and not VC funding, this prompt is obsolete.
   - [pg_evaluation.md](file:///D:/Git/agent-guard/.agent/pg_evaluation.md) (obsolete): A simulated assessment of AgentGuard's business potential. This report contains no technical relevance in the new learning-first context.
2. **Commercial Feasibility Docs (Historic)**:
   - Ensure that any previously deleted feasibility docs or sales decks (like `docs/commercial_feasibility_report.md` which was deleted in history) remain deleted and are not resurrected.

---

## 4. Re-Alignment with the "Main Solid Line" of Technical Focus

To align with the original solid specification in [docs/technical_specification_document.md](file:///D:/Git/agent-guard/docs/technical_specification_document.md) and [docs/implementation_plan.md](file:///D:/Git/agent-guard/docs/implementation_plan.md), the codebase should focus on implementing the deep technical milestones of an agentic operating system, rather than SaaS-specific features. 

The next agent should translate the following **educational/learning tasks** from [docs/unknown_items.md](file:///D:/Git/agent-guard/docs/unknown_items.md) into concrete implementation tickets:

### A. Model Context Protocol (MCP) Safety Middleware (U-03)
* **Goal**: Enable interception and inline policy checks of MCP tool calls.
* **Why it matters**: Currently, if an external agent communicates with tools via MCP, the AgentGuard policy engine is bypassed (blind tool calls). 
* **Implementation strategy**:
  - Implement a proxy middleware wrapper in [agentguard/policy.py](file:///D:/Git/agent-guard/agentguard/policy.py).
  - Integrate tool-call intercepts prior to execution through [check_step](file:///D:/Git/agent-guard/agentguard/policy.py#L75).

### B. Distributed State Consistency via Redlock (U-01)
* **Goal**: Implement distributed locking on memory updates.
* **Why it matters**: Parallel agent dispatch (via LangGraph `Send` API) introduces potential race conditions when writing state changes to the [MemoryManager](file:///D:/Git/agent-guard/agentguard/memory.py) (Redis).
* **Implementation strategy**:
  - Add `pottery` or `redis-py` Redlock functionality inside [agentguard/memory.py](file:///D:/Git/agent-guard/agentguard/memory.py) to manage concurrent writes during parallel node merges.

### C. Async Run Callbacks / Webhooks (U-14)
* **Goal**: Add webhooks notifying external systems of run completions.
* **Why it matters**: Polling `/runs/{id}` creates excessive API traffic. Implementing callback webhooks is a classic distributed system design pattern for long-running agent loops.
* **Implementation strategy**:
  - Add an optional `callback_url` parameter to the run request schema in [backend/schemas.py](file:///D:/Git/agent-guard/backend/schemas.py).
  - Configure the backend to POST the `RunStatusResponse` to the webhook URL upon execution completion.

### D. Production Identity Verification (U-17)
* **Goal**: Implement RS256 token verification with JWKS endpoint validation.
* **Why it matters**: Transitioning from developer-grade symmetric HS256 to asymmetric RS256 teaches how production-grade identity federations (like OAuth/OIDC) interact with security contexts in LLM agent applications.
* **Implementation strategy**:
  - Update [agentguard/auth.py](file:///D:/Git/agent-guard/agentguard/auth.py)'s [decode_token](file:///D:/Git/agent-guard/agentguard/auth.py#L25) to dynamically fetch, cache, and apply public keys from an `AGENTGUARD_JWKS_URL` environment variable.

### E. SDK Library Packaging (U-13)
* **Goal**: Configure standard packaging metadata for installing `agentguard` via `uv` or `pip`.
* **Why it matters**: Learning how to distribute agent safety layers as reusable libraries is highly valuable.
* **Implementation strategy**:
  - Expand [pyproject.toml](file:///D:/Git/agent-guard/pyproject.toml) to configure project metadata, packaging scripts, and standard entry points.

### F. Interactive Policy Dashboard (U-18 Expansion)
* **Goal**: Expand the [frontend/](file:///D:/Git/agent-guard/frontend) React app to support viewing and reloading policy rules.
* **Why it matters**: A major part of the "main solid line" is the declarative governance paradigm. Visualizing the policy rules side-by-side with trace logs significantly improves the user experience.
* **Implementation strategy**:
  - Add a "Policy Manager" tab to the React app.
  - Implement form editors/renders mapped to `/policy` and `/policy/reload` backend endpoints.

---

## 5. Summary of Actions for the Next Agent

The next agent should execute the following steps to finalize the reversion:

1. **Obsolete Asset Removal**:
   - Delete [paul_graham_evaluator.md](file:///D:/Git/agent-guard/.agent/paul_graham_evaluator.md) and [pg_evaluation.md](file:///D:/Git/agent-guard/.agent/pg_evaluation.md) from the workspace.
2. **Spec Re-Alignment**:
   - Update `docs/` and references to omit mentions of "go/no-go" or commercial viability decisions, re-focusing all documentation on the **Supervisor-Worker-Guard** and **Causal Tracing** architecture.
3. **Core Specification Development**:
   - Create a plan to implement the technical backlog outlined in Section 4 (MCP integration, Redlock, Async Webhooks, RS256/JWKS, and Policy Visualization UI).
