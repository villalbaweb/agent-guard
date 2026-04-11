# AgentGuard: Unknown Items & Architectural Questions

## Traceability & Input Needed

| ID | Area | Status | Description | Resolution / Next Step |
| :--- | :--- | :--- | :--- | :--- |
| **U-01** | **Shared Memory Consistency** | ⚠️ Partial | How to handle concurrent writes to Redis from parallel agents? | **Implemented:** `MemoryManager` uses Redis with silent in-memory fallback. Concurrent write safety (Redlock) not yet implemented — safe for the current serial executor but required before switching to parallel `Send`. **Next:** Integrate `Redlock` in `MemoryManager` when LangGraph `Send` is adopted. |
| **U-02** | **Policy Latency** | ✅ Resolved | What is the total latency of in-line policy checks per step? | **Resolved via cloud embeddings:** Local sentence-transformers Docker (~4 GB) was evaluated and rejected. Cloud embedding APIs (Google `gemini-embedding-001`, OpenAI `text-embedding-3-small`) add ~200–400ms per step — acceptable for current use cases. Sidecar architecture remains an option for sub-100ms requirements at scale. |
| **U-03** | **MCP Integration** | ⏳ Open | How will the Model Context Protocol integrate with the `PolicyGuard` node? | **Strategy:** Implement `PolicyGuard` as an MCP-aware proxy middleware. Each MCP tool call passes through `check_step()` before execution. No implementation yet — blocked on selecting MCP server framework. |
| **U-04** | **Human-in-the-Loop (HITL)** | ⚠️ Partial | How to implement asynchronous "pause and approve" in the recursive graph? | **Partial:** `GovernanceDecision.hitl_required` field added. `require_hitl` rule action propagates to executor output as `HITL_PENDING`. **Remaining:** Wire LangGraph `interrupt()` to pause the graph and resume after external approval signal. Validate propagation through nested subgraphs. |
| **U-05** | **Identity Mapping** | ⏳ Open | How to propagate the "root user" identity through all worker subgraphs? | **Strategy confirmed:** JWT-based "Governance Identity" token passed through `AgentGuardState`. **Remaining:** Add `auth_context` field to `AgentGuardState`, implement JWT generation at entry point, validate propagation into child graph states. |
| **U-06** | **Cost Attribution** | ⏳ Open | How to attribute costs of cached results from the `BlueprintManager`? | **Still open.** Blueprint cache is implemented in `MemoryManager` but cost attribution for cached vs. freshly computed results is undefined. **Proposed:** Tag each blueprint with its generation cost; amortize across consumers. |
| **U-07** | **Governance Resilience** | ⏳ Open | No circuit breakers defined for the governance layer itself. | **Partially mitigated:** LLM semantic guard fails closed (blocks on exception). Redis unavailability degrades to in-memory fallback — governance continues. **Remaining:** Define graduated failsafe levels: (1) rules-only mode if LLM guard fails, (2) full block if both LLM and rules fail. |

---

## New Items Identified During PoC

| ID | Area | Description | Proposed Next Step |
| :--- | :--- | :--- | :--- |
| **U-08** | **Embedding Cache** | Every `check_step()` call embeds the current thought, including near-identical repeated calls. No caching of embedding vectors. | Cache embedding vectors by content hash in Redis. Significant cost/latency reduction for recursive runs where subtasks are similar. |
| **U-09** | **Registry Vector Search** | `Registry.search_by_intent()` uses substring matching. Intent routing accuracy degrades as the number of registered agents grows. | Replace with vector similarity search (embeddings against agent `semantic_description` fields), backed by Redis VSS or pgvector. |
| **U-10** | **Parallel Execution Safety** | `RecursiveExecutor` uses a serial `for` loop over subtasks. True parallelism with LangGraph `Send` is blocked by U-01 (no Redlock yet). | Implement Redlock in `MemoryManager`, then migrate executor to `Send`. Required for production throughput targets. |

---

*Referenced Sections:*
- *Implementation Plan: Phases 1–5*
- *TSD: Section 6 (State Management)*
- *TSD: Section 8 (Security & Safety)*
- *Modernization Doc: Section 8 (Remaining Gaps)*
