# AgentGuard

An **educational agentic-AI playground** for exploring governed, recursive multi-agent systems.

## What this is / is NOT

- **IS** a learning testbed for agentic-AI concepts: governance, observability, recursive execution, identity propagation, and policy-driven safety.
- **IS NOT** a commercial product, has no roadmap to GA, no SLA, and is not intended for production deployment.

## Architecture

AgentGuard uses a **Supervisor-Worker-Guard** pattern built on [LangGraph](https://github.com/langchain-ai/langgraph):

```
User / REST Client
        │
        ▼
   FastAPI Backend  (backend/)
        │
        ▼
   RecursiveExecutor  (agentguard/executor.py)
   ┌────────────────────────────────────────────┐
   │ preflight → decompose → plan → execute →   │
   │ synthesize  (PolicyEngine check every step) │
   └────────────────────────────────────────────┘
        │                    │
        ▼                    ▼
   MemoryManager         LLM Provider        DatabaseManager
   (Redis / in-mem)      (Gemini/Claude/…)   (PostgreSQL/pgvector)
```

Key subsystems:

| Subsystem | File | What you can learn |
|:----------|:-----|:-------------------|
| Causal Dependency Graph | `agentguard/trace.py` | How agent reasoning chains are recorded and visualized |
| Policy Engine | `agentguard/policy.py` | Inline safety checks, budget enforcement, HITL triggering |
| Shared Epistemic Memory | `agentguard/memory.py` | Redis-backed state, loop detection, blueprint caching |
| Capability Registry | `agentguard/registry.py` | Dynamic agent discovery via pgvector semantic search |
| Identity Propagation | `agentguard/auth.py` | JWT auth context threading through recursive subgraphs |
| React Visualizer | `frontend/` | Cytoscape-based causal graph UI |

## Quick Start

```bash
# Start infrastructure (Redis)
docker compose -f docker-compose.poc.yml up -d

# Start API
uv run uvicorn backend.main:app --reload --port 8000

# Start frontend
cd frontend && npm install && npm run dev
```

Copy `.env.example` to `.env` and fill in at least one LLM provider key.

## Documentation

| Doc | Contents |
|:----|:---------|
| `docs/technical_specification_document.md` | Full architecture, data model, API reference |
| `docs/implementation_plan.md` | Architecture reference and component guide |
| `docs/unknown_items.md` | Open questions and research backlog |
| `docs/testing_guide.md` | How to run the test suite |

## Research Backlog

The frontier learning targets are tracked in `docs/unknown_items.md`. Highlights:

- **F-01** Reflection / self-critique loop
- **F-02** Dynamic replanning on failure
- **F-03** Multi-agent debate / consensus
- **F-04** Evaluation harness (policy ON vs OFF measurement)
- **F-05** MCP Safety Middleware — governance interception for MCP tool calls

## Examples

`examples/asset_management.py` — a finance research scenario demonstrating multi-agent decomposition, per-scenario policy scoping, and causal tracing. Run it with `uv run python examples/asset_management.py`.
