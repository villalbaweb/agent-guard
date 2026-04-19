# MVP Architecture: AgentGuard

**Core Assumption**
Developers want a drop-in governance layer for agents that handles HITL (Human-in-the-loop) and policy checks without requiring them to write custom LangGraph state management logic.

**Minimum Feature Set**
1. The Python SDK (`agentguard` module) with the `RecursiveExecutor`.
2. A single YAML file loader for policies (budget and basic regex content filters).
3. The ability to trigger a HITL pause and resume it via a simple Python function call.
4. In-memory state and registry (no Postgres, no Redis).

**What Gets Cut (Backlog)**
- The FastAPI backend (REST API). Developers can use the SDK directly.
- Multi-tenant scoping.
- PostgreSQL / pgvector dynamic registry (in-memory only).
- Redis shared epistemic memory.
- Advanced JWT authentication (simple string-based roles for now).

**Test Criteria**
A developer successfully imports `agentguard`, configures a 3-step agent workflow, intentionally triggers a policy violation (e.g., spending over $1.00), and the system correctly pauses execution and waits for human approval before continuing.

**2-Week Launch Plan**
- **Days 1-3:** Strip out Redis, Postgres, and FastAPI dependencies from the core `agentguard` module. Ensure it runs purely in-memory.
- **Days 4-6:** Polish the developer experience of the SDK. Make `make_initial_state` and graph invocation extremely clean.
- **Days 7-8:** Write a comprehensive `README.md` and a single, flawless Jupyter notebook example showing a governed agent workflow.
- **Days 9-10:** Package for PyPI (`pip install agentguard-core`).
- **Days 11-14:** Pair program with 3 design partners to integrate the pip package into their codebases and observe where they get stuck.