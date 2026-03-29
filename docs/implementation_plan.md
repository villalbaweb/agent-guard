# AgentGuard: Implementation Plan (MVP)

## Goals and Non-Goals
- **Goal:** Unify the recursive, parallel execution of SSA (Project B) with the governance and policy engine of ACP (Project A).
- **Goal:** Reach an MVP that can handle multi-step, complex tasks (e.g., "Analyze the security of X and suggest fixes") while enforcing safety and cost constraints.
- **Non-Goal:** Full UI development.
- **Non-Goal:** Multi-cloud/multi-region deployment beyond local/single-cluster.

## System Overview
AgentGuard will use a **Supervisor-Worker-Guard** architecture.
1.  **Orchestrator (SSA):** Decomposes tasks, creates blueprints, and manages recursive worker subgraphs.
2.  **Control Plane / Guard (ACP):** Intercepts every step to evaluate policies, check for loops, and enforce budget limits.

## Agentic Architecture
We will implement the **Fractal Chain-of-Thought (FCoT)** and **Supervisor** patterns from the NotebookLM.

### Key Components and Services
- **Global Orchestrator:** The primary entrance for user tasks; uses SSA's `TaskDecomposer` and `BlueprintManager`.
- **Governance Node (Guard):** A shared LangGraph node that runs before and after any worker action. It uses ACP's `PolicyEngine` and `LoopSupervisor`.
- **Recursive Executor:** A lightweight subgraph for parallel task execution (SSA's `RecursiveExecutor`).
- **Telemetry & Traceability:** Uses ACP's "thoughts" recording and SSA's agent/edge logging to build a **Causal Dependency Graph**.
- **Shared Epistemic Memory:** A Redis-based global state for history, embeddings (loop detection), and cached blueprints.

## LangGraph Graph Design
1.  **Entry Node:** `task_decomposer` (SSA)
2.  **Governance Check:** `preflight_policy_check` (ACP) -> Blocks if forbidden or over budget.
3.  **Planner Node:** `execution_planner` (SSA) -> Creates the multi-agent blueprint.
4.  **Executor Node:** `graph_executor` (SSA) -> Orchestrates the specialized workers.
    - Each **Worker** is actually a `recursive_subgraph` (SSA).
    - Each **Step** within a worker is wrapped in a **Governance Check** (ACP).
5.  **Synthesizer Node:** `synthesizer` (SSA) -> Compresses and summarizes the final output.
6.  **Final Review:** `fidelity_audit` (NotebookLM pattern) -> Final check against original user instructions.

## MVP Scope
- **Phase 1: Project Skeleton, State & Registry**
    - Merge `AgentState` (SSA) and `GovernanceDecision` (ACP) into a unified `AgentGuardState`.
    - **Capability Registry:** Implement a central `Registry` module where agents and MCP tools register their metadata (name, description, schemas).
    - Set up the Redis-based shared memory for loop detection and history.

- **Phase 2: Governance & Intent Routing**
    - **Intent-Based Router:** Update the `ExecutionPlanner` to use the **Agent Router** pattern (Intent Extraction -> Capability Graph Lookup).
    - Integrate `PolicyEngine` (ACP) as a mandatory check for all tool calls and task transitions.
    - Implement the `LoopSupervisor` (ACP) to detect semantic oscillations in recursive tasks.

- **Phase 3: Recursive Execution Porting**
    - Port the `RecursiveExecutor` (SSA) to the new state model.
    - Implement the `MiniPlanner` for parallel task decomposition.

- **Phase 4: Telemetry & Observability**
    - Build the unified logging system to produce a machine-readable **Causal Dependency Graph**.
    - Implement the "thoughts" telemetry for real-time monitoring.

- **Phase 5: Validation & Benchmarking**
    - Run the integrated system against complex, multi-step scenarios.
    - Verify that policies (PII, forbidden topics, cost) are strictly enforced.

## Milestones and Phases
- **Phase 0: Governance Stress Test & Latency PoC (Architect Request)**
    - **Objective:** Measure "governance tax" and validate distributed system stability under recursive load.
    - **Task: "Fork Bomb" PoC:** Build a 3-level deep recursive task that triggers concurrent tool calls.
    - **Measure:** Exact latency, token usage, and Redis connection overhead.
    - **Goal:** Establish baseline for Asymmetric Control Plane (Sidecar) migration.
- **Milestone 1:** Unified State, Registry & Shared Memory (Phase 1)
- **Milestone 2:** Autonomous Execution with Intent Routing & Hard Stops (Phase 2 & 3)
- **Milestone 3:** Full Traceability & Audit Trail (Phase 4)

## Risks and Mitigations
- **Risk:** High latency from semantic loop checks.
    - *Mitigation:* Cache embeddings and use high-performance similarity search (e.g., FAISS or Redis VSS).
- **Risk:** Recursive depth leading to state bloat.
    - *Mitigation:* Implement the SSA `Summarizer` node at every recursion level.
- **Risk:** Complexity of the unified graph.
    - *Mitigation:* Use subgraphs to isolate execution logic from governance logic.

---
*Patterns Referenced:*
- *Supervisor Architecture [NotebookLM]*
- *Fractal Chain-of-Thought (FCoT) [NotebookLM]*
- *Real-Time Compliance Monitoring [NotebookLM]*
- *Shared Epistemic Memory [NotebookLM]*
