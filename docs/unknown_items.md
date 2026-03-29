# AgentGuard: Unknown Items & Architectural Questions

## Traceability & Input Needed

| ID | Area | Description | Why Unknown / Evidence Missing | Proposed Next Step / Question |
| :--- | :--- | :--- | :--- | :--- |
| **U-01** | **Shared Memory Consistency** | How to handle concurrent writes to the Redis-based shared state from parallel agents? | **STRATEGY:** Use Redis `SETNX` for distributed locking on specific `run_id` keys and force worker subgraphs to return state to parent orchestrator rather than writing directly. | Implementation: Integrate `Redlock` or similar in `MemoryManager`. |
| **U-02** | **Policy Latency** | What is the total latency impact of running in-line policy checks (embeddings + PII scan) for every agent step? | **STRATEGY:** Move to Asymmetric Control Plane (Sidecar) for non-destructive policies. | Research: Benchmark sidecar vs. inline latency. |
| **U-03** | **MCP Integration** | How will the Model Context Protocol (MCP) integrate with the `PolicyGuard` node? | The current SSA logic uses standard tools; MCP integration needs a unified middleware. | Architect: Can the `PolicyGuard` be implemented as an MCP-aware proxy? |
| **U-04** | **Human-in-the-Loop (HITL)** | How to implement an elegant, asynchronous "pause and approve" for the recursive graph? | LangGraph `interrupt` works for top-level, but its behavior in nested subgraphs needs validation. | Research: Test LangGraph `interrupt` propagation across recursive levels. |
| **U-05** | **Identity Mapping** | How to map the "root user" identity down to the execution layer of an agent? | **STRATEGY:** Implement a "Governance Identity" token (JWT) passed through the graph state. | Implementation: Update `AgentGuardState` schema to include `auth_context`. |
| **U-06** | **Cost Attribution** | How to attribute costs of "cached" results from the `BlueprintManager`? | If a result is cached, who "pays" for the initial generation? | Architect: Define a policy for "Cost Allocation" of shared blueprints. |
| **U-07** | **Governance Resilience** | No circuit breakers defined for the governance layer itself. | If the policy engine fails open, compliance is breached; if it fails closed, the system halts. | Design: Implement a "Failsafe" mode with graduated levels of protection. |

---
*Referenced Sections:*
- *Implementation Plan: Phase 2 & 4*
- *TSD: Section 6 (State Management)*
- *TSD: Section 8 (Security & Safety)*
