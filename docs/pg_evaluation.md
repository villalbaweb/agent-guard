# PG Evaluates: AgentGuard

*Evaluated by: Paul Graham (simulated) | Date: 2026-04-22*

---

## 1. The Organic Test

**Mixed signal.** The code reads like someone who actually ran an agent fleet and got burned. The `AgentGuardState` carries a causal trace, per-tenant policy hot-reload, budget ceilings enforced *inline* during the graph execution — not bolt-on middleware. That's the tell. Nobody writes `policy.yaml` hot-reloading as a first feature unless they've been paged at 3am because a LangChain agent spent $400 on embeddings.

But: the README-shaped docs, the "AgentGuard platform" framing — that's the smell of someone who read a VC deck about "AI governance" and is backing into a thesis. The toy hasn't fully turned into a tool yet. There's a POC file (`poc.py`) sitting next to a "backend" with routes. That's a fork in the road — which one is the real product?

---

## 2. The Secret

Stated secret: *"There's a governance gap in autonomous agent fleets."* That's not a secret. That's a McKinsey slide.

The *actual* secret, hiding in the code: **model providers will never ship the audit layer you need, because it would require them to admit their models are non-deterministic liability engines.** OpenAI can't sell you "here's a causal trace proving our model obeyed your budget" — it implies the inverse case exists. So the governance layer has to live *outside* the provider. That's defensible. That's the real insight.

The founder needs to stop saying "governance gap" and start saying *that* out loud. The pitch is buried.

---

## 3. Relentless Resourcefulness — Architecture Read

Looking at `orchestrator.py`, `state.py`, `policy.py`, `registry.py`: **this is not a thin wrapper.** A thin wrapper is 200 lines calling `openai.chat.completions.create` with a system prompt about "safety." This has:

- Recursive decomposition with bounded depth (necessary — agents loop)
- pgvector-backed intent registry (necessary — routing without vectors is grep)
- Per-tenant YAML policy with hot reload (necessary — multi-tenant or die)
- LangGraph state machine with explicit HITL nodes (necessary — you need a pause primitive)

Is there accidental complexity? **Yes.** Redis + pgvector + LangGraph + a separate FastAPI backend + Docker compose + two LLM factories — for a POC, this is three too many moving parts. The founder is building the cathedral before the shack has running water. That's a warning sign but a fixable one.

The LangGraph choice specifically worries me. LangGraph is a framework whose abstractions may not survive contact with production. Betting the foundation on it is a risk. The insight layer (policy + trace + registry) is the durable piece — it could live on anything.

---

## 4. The Market Pivot

If "enterprise AI governance" is a lie (and it probably is — enterprises buy governance from Palo Alto Networks, not a startup), **what's the path?**

The path I see: **the audit trail becomes the product.** Not the enforcement. Every serious agent deployment will eventually need a machine-readable record of *why the agent did what it did* — for debugging, for insurance, for litigation. AgentGuard is accidentally building the Datadog of agent reasoning. The "policy enforcement" is the wedge; the causal trace is the moat.

Second path: **the registry.** A pgvector-backed intent-to-tool router is a capability that every agent framework will need and none of them want to build. That could be a product on its own.

The project is standing on a layer of the stack — the "what did this agent do and why" layer — that nobody owns yet. That's colonization, not toolmaking. Good.

---

## 5. Naughtiness

Building an *independent* governance layer instead of waiting for OpenAI/Anthropic/Google to ship one is the correct kind of naughty. It assumes the platforms will fail to solve this, which they will, because it's not in their interest to. That's the right bet.

What's *missing* in naughtiness: the project respects the provider APIs too much. Where's the proxy layer that sits between the agent and the LLM and injects the policy into the prompt itself? Where's the deterministic replay that doesn't trust the provider's "temperature=0"? The team isn't quite paranoid enough yet.

---

## Leaks and Risks

1. **Too many dependencies for the current state.** Drop Redis until you can't live without it.
2. **"Governance" positioning is soft.** Rename around *causal trace* or *agent audit* and the pitch sharpens 5x.
3. **LangGraph lock-in.** Policy and trace logic should be able to run on a raw state dict. Don't marry the framework.
4. **No clear single design partner visible in the code.** Who is the first customer whose agent fleet is melting? That shapes everything.
5. **Docs-to-code ratio is inverted for a seed-stage project.** You don't need "detailed engineering and architecture documentation" yet. You need 10 users.

---

## Decision

**Watch.** Leaning toward Fund if the founder can answer one question: *"Who is the specific person whose agent pager is going off, and when did you last talk to them?"* If the answer is a real name and a date this month, it's Fund. If the answer is "we're targeting the enterprise AI governance TAM" — No Fund.

---

## Relentless Resourcefulness Score

**7 / 10**

The machinery is real. The code shows someone who builds instead of writes decks. The thesis is buried under buzzwords but the bet underneath it is correct. Points off for premature architectural scope and soft positioning. Points on for picking a layer of the stack that the big players structurally can't own.

Fix the pitch, ship to 3 painful users, drop Redis. Come back in 90 days.
