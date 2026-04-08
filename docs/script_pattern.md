# AgentGuard: Core Scripts and Agentic Design Patterns

This document serves as a reference for the `agentguard/` package, detailing the function of each script and the specific agentic design patterns they enforce.

---

## 1. Foundation & State

### [state.py](file:///d:/Git/agent-guard/agentguard/state.py)
*   **Function:** Defines the unified `AgentGuardState` TypedDict used across the entire LangGraph.
*   **Agentic Pattern:** **Stateful Agent Mesh.**
    *   It ensures that all nodes (Orchestrator, Policy, Executor) share a "Single Source of Truth."
    *   It tracks recursive metadata (depth, root IDs) to maintain context across complex tasks.

### [llm.py](file:///d:/Git/agent-guard/agentguard/llm.py)
*   **Function:** A factory tool that initializes the appropriate LLM provider (Gemini, Claude, GPT) based on environment variables.
*   **Agentic Pattern:** **Model-Agnostic Interfacing.**
    *   Allows the system to swap the "brain" of any agent without changing the orchestration logic.
    *   Supports the 2026 standard of multi-provider resilience.

---

## 2. Infrastructure & Discovery

### [registry.py](file:///d:/Git/agent-guard/agentguard/registry.py)
*   **Function:** Manages the registration and lookup of specialized agents and tools.
*   **Agentic Pattern:** **Capability Registry ("Yellow Pages").**
    *   Enforces intent-based discovery over hard-coded function calls.
    *   Requires schemas for every tool, ensuring input/output contracts are strictly followed.

### [memory.py](file:///d:/Git/agent-guard/agentguard/memory.py)
*   **Function:** Acts as the "Short-Term/Shared" memory for runs, tracking thought history and caching patterns.
*   **Agentic Pattern:** **Shared Epistemic Memory.**
    *   **In-Line Loop Detection:** Prevents agents from repeating the same failed logic by comparing current intent against history.
    *   **Blueprint Caching:** Enables the reuse of successful execution plans to reduce latency and cost.

---

## 3. Intelligence & Control

### [orchestrator.py](file:///d:/Git/agent-guard/agentguard/orchestrator.py)
*   **Function:** Handles the high-level decomposition of user tasks into actionable sub-intents.
*   **Agentic Pattern:** **Strategic Planner (SSA - Stateful Strategic Agent).**
    *   It separates "Thinking" (Decomposition/Planning) from "Doing" (Execution).
    *   Enforces **Intent-Based Routing**, mapping semantic user goals to registered agent roles.

### [policy.py](file:///d:/Git/agent-guard/agentguard/policy.py)
*   **Function:** The central control plane that validates actions for safety, budget, and compliance.
*   **Agentic Pattern:** **Watchdog / Semantic Guardrail (ACP - Agentic Control Plane).**
    *   **Zero-Trust Execution:** No agent action is taken without a per-step policy check.
    *   **Semantic Safety:** Uses an LLM to analyze the *meaning* of an action, providing a layer of security that keyword filters cannot match.

---

## 4. Execution & Orchestration

### [executor.py](file:///d:/Git/agent-guard/agentguard/executor.py)
*   **Function:** The central "engine room" that builds and executes the LangGraph.
*   **Agentic Pattern:** **Orchestrator & Recursive Subgraph.**
    *   **Deterministic Workflow:** Enforces a strict Preflight -> Plan -> Execute -> Synthesize lifecycle.
    *   **Recursive Decomposition:** Allows the system to spawn child executors to solve sub-problems at arbitrary depths, maintaining state-consistency throughout.

### [poc.py](file:///d:/Git/agent-guard/agentguard/poc.py)
*   **Function:** A comprehensive integration test and entry point for the framework.
*   **Agentic Pattern:** **MAS (Multi-Agent System) Orchestration.**
    *   Demonstrates how the individual patterns (Policy, Registry, Executor) work together to solve a complex "Fork Bomb" stress test.
