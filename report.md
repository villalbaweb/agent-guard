# Comparison Report: AgentGuard vs. Zora

## 1. Introduction

This report compares **AgentGuard** and **Zora**, two distinct AI agent systems designed for different use cases and environments.

- **AgentGuard** is an enterprise-grade, high-performance, policy-driven agentic operating system. It relies on a "Supervisor-Worker-Guard" architecture that integrates recursive task execution with a strict inline governance and policy engine. It's built in Python and targets backend orchestrations.
- **Zora** is a local, personal AI agent meant to run securely on a user's machine (macOS/Linux). It focuses on extreme security via a default-deny posture, performing tasks locally such as file manipulation, coding chores, and personal automation, without the risk of prompt injection or runaway actions. It's built primarily using Node.js/TypeScript.

---

## 2. Feature Comparison

| Feature | AgentGuard | Zora |
| --- | --- | --- |
| **Primary Use Case** | Enterprise autonomous tasks, backend orchestration, recursive research | Personal automation, local file management, dev chores |
| **Architecture** | Supervisor-Worker-Guard, FCoT (Fractal Chain-of-Thought) | Local Daemon, CaMeL channel quarantine, 6-hook tool pipeline |
| **Governance / Safety** | Inline Policy Engine (ACP), Budget checks, LLM-based semantic safety checks | Static `policy.toml` loaded pre-action, human-in-the-loop approval, irreversibility scoring |
| **Skill/Tool Ecosystem** | Capability Registry (Redis-backed "Yellow Pages"), MCP integration | Local `.skill` files scanned by AST for malicious patterns (no central hub) |
| **State & Memory** | Shared Epistemic Memory (Redis), LangGraph Checkpointers | Local filesystem memory (`~/.zora/memory`), loads state between sessions |
| **Routing** | Intent-based routing with LLM intent extraction | Direct commands/prompts from the user or automated routines |
| **Loop / Recursion** | Recursive Executor (Depth N), Semantic Loop Detection | Runaway loop prevention via strict "Action Budget" |

---

## 3. Tech Stack Comparison

| Component | AgentGuard | Zora |
| --- | --- | --- |
| **Primary Language** | Python | TypeScript (Node.js) |
| **Agent Framework** | LangGraph, LangChain | Custom daemon structure, integration with Claude Agent SDK |
| **Data Storage / State** | Redis, LangGraph Checkpointers | Local filesystem (plain text, `.toml`, `.json`) |
| **LLM Support** | OpenAI, Anthropic, Google Vertex AI / GenAI (via LangChain) | Claude Code session, Google Gemini (auth via existing CLI sessions) |
| **Communication Channels** | API/CLI Gateway (internal to orchestrator) | Signal, Telegram (E2E encrypted channels) |

---

## 4. Strengths and Weaknesses

### 4.1. AgentGuard

**Strengths:**
- **Deep Recursion:** Excellent at decomposing complex tasks into subtasks (N levels deep).
- **Enterprise Governance:** Built-in budgeting, cost attribution, and LLM-based inline policy checks.
- **Dynamic Routing:** Intent-based routing using a registry to match subtasks with the best available specialized agents.
- **Traceability:** Produces a Causal Dependency Graph for 100% auditable reasoning chains.

**Weaknesses:**
- **Infrastructure Overhead:** Requires external dependencies like Redis and LangGraph.
- **Setup Complexity:** Oriented towards cloud or backend setups, making it harder for an individual user to install and run locally as a daily assistant.
- **Latency Risks:** Inline semantic safety checks and LLM-based routing can increase overall execution latency.

### 4.2. Zora

**Strengths:**
- **Uncompromising Security:** "Locked by Default" posture, with policies living in a static config file (`policy.toml`) immune to context compaction.
- **No Surprises:** Leverages existing CLI subscriptions (no new API keys), meaning zero hidden bills or token costs.
- **Local Autonomy:** Runs entirely on the local machine with direct filesystem access and specific, bounded shell commands.
- **Human-in-the-Loop:** Automatically flags risky actions based on an "irreversibility score" and routes to user's phone for approval.

**Weaknesses:**
- **Limited Scalability:** Designed for personal use, not for handling massive enterprise-scale background workloads.
- **Manual Extension:** No centralized skill marketplace; users must manually install and vet local `.skill` files.
- **Platform Limitations:** Currently optimized for macOS/Linux (Windows support pending).

## 5. Conclusion

**AgentGuard** is the ideal solution for an organization looking to build a complex, multi-agent orchestration backend where budgeting, compliance, and deep task decomposition are paramount.

**Zora**, on the other hand, is built for the individual developer or power user who wants a secure, capable, local AI assistant that can automate daily computer chores without any risk of system compromise or unexpected API costs.