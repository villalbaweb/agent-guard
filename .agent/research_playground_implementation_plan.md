# AgentGuard → Research Playground: Implementation Plan

> **Status:** Proposed
> **Supersedes (as the actionable plan):** `revert_to_core_spec_analysis.md` (kept as historical context)
> **Date:** 2026-06-07
> **Goal:** Re-establish AgentGuard as an **educational / research playground for advanced agentic AI**, remove all commercial go/no-go artifacts, retain and enhance the valuable traceability + governance engineering, and add a *frontier* learning backlog.

---

## 0. Guiding Principles

These principles govern every decision below. When in doubt, apply them.

1. **Learning-first, not product-first.** Every feature exists to teach an agentic-AI concept (governance, observability, recursion, routing, safety). We measure success by *insight gained*, not adoption.
2. **Remove the verdict, keep the machinery.** Delete VC/go-no-go *judgments*. Keep — and reframe — the engineering concepts they were attached to (multi-tenant scoping, identity federation, SDK packaging). These are excellent learning targets.
3. **Reframe, don't lobotomize.** "Customer/tenant/enterprise" → "scenario/experiment/persona". The *mechanisms* (per-scenario policy, identity propagation, layered inheritance) stay; only the sales narrative goes.
4. **Engineering vocabulary is not commercial vocabulary.** Words like "production", "startup" (process startup), and CORS hardening notes are legitimate and must NOT be stripped.
5. **Bias toward observability.** Anything that makes agent behavior *visible* (traces, the React visualizer, eval harness) is the heart of the playground and gets priority.

---

## 1. Phase A — Cleanup & Reframing (Remove all go/no-go traces)

### A1. Delete obsolete commercial artifacts
| File | Action | Rationale |
|:-----|:-------|:----------|
| `.agent/paul_graham_evaluator.md` | **Delete** | LLM role-play prompt for VC evaluation. Pure go/no-go. |
| `.agent/pg_evaluation.md` | **Delete** | Simulated "Fund / No Fund / Watch" assessment. Zero technical value in research context. |
| `docs/commercial_feasibility_report.md` | **Verify stays deleted** | Already removed in history (`f57ca81`, `fa8ae1d`). Confirm it is not resurrected. |

> Note: `revert_to_core_spec_analysis.md` references these files. After deletion, leave the analysis doc as-is (historical) OR add a one-line "[resolved by research_playground_implementation_plan.md]" banner at its top. Recommended: keep it untouched as a record of the decision.

### A2. Reframe documentation (edit, do not delete)
The following are **reframes**, not removals. Preserve all technical content; change only the framing language.

| Location | Current framing | Reframe to |
|:---------|:----------------|:-----------|
| `docs/technical_specification_document.md` §1 Introduction | "enterprise-grade autonomous tasks" | "a research testbed for governed, recursive multi-agent systems" |
| `docs/technical_specification_document.md` §12 "Customer Interaction Modes" | "Customers interact… targeting a different persona" | "Interaction Surfaces" — keep all 4 surfaces (REST, SDK, Policy YAML, Scoping) as *learning surfaces*; rename "Target persona: Platform/DevOps teams" → "Use this surface when exploring: …" |
| `docs/technical_specification_document.md` §12.4 "Per-Tenant Rules" | "tenant", "SaaS liability" | "Per-Scenario Rules"; keep Options A/B/C (they teach layered policy design) but reframe "critical for SaaS liability" → "demonstrates immutable platform safeguards vs. overridable overlays" |
| `docs/unknown_items.md` "Customer Integration Readiness Analysis" heading + U-13..U-18 | "enterprise customer", "shippable product", "enterprise buyers" | "Learning Backlog — Production-Grade Concepts to Explore"; reframe each item's *motivation* sentence from a sales need to a learning objective (mechanics & next steps stay identical) |
| `docs/implementation_plan.md` header "MVP state" | "Next milestone: SDK packaging and API enhancements" | "Next: frontier agentic experiments (see research backlog)" |

**Explicitly KEEP unchanged** (legitimate engineering language, not commercial):
- `agentguard/auth.py` "never use in production" warnings
- `backend/main.py` CORS "restrict in production" note (this is a real security note — see B-tier U-16)
- `agentguard/db.py` "at startup" (process startup)
- `examples/policy_meridian.yaml` and `asset_management.py` — a finance demo is a perfectly good *research scenario*; keep it, optionally add a header comment clarifying it is illustrative.

### A3. Add a project-identity anchor
- **Create `README.md`** (repo currently has none at root) stating the project is an **educational agentic-AI playground**, summarizing the Supervisor-Worker-Guard + Causal Tracing architecture, and linking `docs/`. This is the single most effective anti-drift measure — it stops future contributors re-introducing product framing.
- Add a one-paragraph "What this is / is NOT" section: *IS a learning testbed; is NOT a commercial product, has no roadmap to GA, no SLA.*

### A4. Verification
- Re-run the repo-wide grep for `paul graham|go.?no.?go|fund / no fund|investor|VC|feasibility|pitch` → expect **zero** hits outside `revert_to_core_spec_analysis.md`.
- `git grep` for `Customer Integration Readiness` → confirm reframed.

---

## 2. Phase B — Retain & Enhance Core Observability (the heart of the playground)

These already exist and are the project's best assets. Enhance, don't rebuild.

| Asset | File | Enhancement for learning value |
|:------|:-----|:-------------------------------|
| Causal Trace builder | `agentguard/trace.py` | Add a `schema_version: "1.1"` field set for per-event LLM token counts + latency breakdown (so learners can *see* where time/cost goes). |
| Trace HTTP endpoint | `backend/routes/runs.py` `GET /runs/{id}/trace` | Keep. Add optional `?format=mermaid` to emit a Mermaid diagram of the causal graph for docs/notebooks. |
| React visualizer | `frontend/src/App.tsx` | Keep Cytoscape graph. Add a side panel showing the *policy decision* attached to each node (decision, reason, cost) — connects governance to behavior visually. |
| Policy Dashboard | `frontend/` (new tab) | **(from analysis §4.F)** Add a "Policy" tab wired to `GET /policy` + `POST /policy/reload`. Read-only first, then form editors. High pedagogical value: edit a rule, re-run, watch the trace change. |

---

## 3. Phase C — Technical Backlog (Tiered)

The analysis (§4) proposed a flat backlog. I've **re-tiered it by learning value for "edge agentic AI"**, and added a frontier tier the analysis omitted. Items map to `unknown_items.md` IDs where they exist.

### Tier 1 — Frontier Agentic Capabilities (highest learning value; NEW)
These directly explore "edge agentic AI tech" and were missing from the original backlog.

- **F-01 · Reflection / Self-Critique loop.** Add an optional `reflect` node after `synthesize` that critiques the answer against the original task and can trigger one re-plan. Teaches the reflection pattern; every reflection emits trace events so you can *see* the agent reconsidering.
- **F-02 · Dynamic replanning on failure.** When a worker fails or a policy blocks a step, let the planner attempt an alternative agent/route instead of hard-failing. Teaches error-recovery in agent graphs; surfaces as alternate edges in the causal trace.
- **F-03 · Multi-agent debate / consensus (experimental).** A scenario where N workers answer independently and a judge node reconciles. Pairs naturally with the existing parallel `Send` fan-out and the registry.
- **F-04 · Evaluation harness.** A `scripts/` or `agentguard/eval.py` that runs a fixed scenario set with policy ON vs OFF and reports *whether governance changed behavior/cost/safety*. This is the experiment that justifies the whole project — without it the playground can't measure anything.
- **F-05 · MCP Safety Middleware** *(= U-03, promoted to frontier).* Proxy/wrapper so MCP tool calls pass through `PolicyEngine.check_step()` before execution. Highest-value item from the original analysis — it's where agent safety meets the emerging tool-call standard. Implement in `agentguard/policy.py` + a thin MCP adapter; gate behind an optional dependency group.

### Tier 2 — Distributed-Systems Concepts (solid learning; from analysis §4)
- **D-01 · Distributed locking / Redlock** *(= U-01).* Add Redlock (`pottery` or `redis-py`) around `MemoryManager` writes; demonstrate the race with parallel `Send`, then fix it. Teaches concurrency safety. Include a test that *reproduces* the race before the fix.
- **D-02 · Async run webhooks** *(= U-14).* Optional `callback_url` on `RunCreateRequest`; POST `RunStatusResponse` on completion. Classic long-running-job pattern.
- **D-03 · Governance circuit breaker** *(= U-07).* Graduated failsafe: rules-only mode if LLM guard fails → full block if both fail. Teaches fail-closed design.

### Tier 3 — Production-Grade Concepts (reframed as learning, from analysis §4)
- **P-01 · RS256 / JWKS identity** *(= U-17).* Dynamic public-key fetch+cache from `AGENTGUARD_JWKS_URL` in `auth.py::decode_token`. Keep HS256 for dev. *Learning objective:* how OIDC/OAuth identity federation meets agent security contexts.
- **P-02 · SDK packaging** *(= U-13).* Flesh out `pyproject.toml` `[project]` metadata + entry points; add `CHANGELOG.md`. *Learning objective:* how to distribute a reusable agent-safety layer. (No PyPI publish required for a playground; local `uv`/`pip -e` install is the goal.)
- **P-03 · CORS startup warning** *(= U-16).* Log a warning when `ALLOWED_ORIGINS` defaults to `*`. Small, good hygiene, keeps the security note from A2 honest.

### Deferred / Out of scope
- Anything purely about selling, billing, SLAs, marketplace monetization, or multi-tenant *commercial* isolation. Multi-tenant *mechanics* (U-11, already implemented) stay; the commercial wrapper does not.

---

## 4. Suggested Sequencing

```
Phase A (cleanup + reframe + README)      ← do first, single focused PR, low risk
   │
   ├─ Phase B (observability enhancements) ← these make every later experiment legible
   │
   └─ Phase C
        Tier 1: F-04 eval harness  → F-05 MCP  → F-01 reflection  → F-02 replanning → F-03 debate
        Tier 2: D-01 Redlock (before relying on parallel Send) → D-02 webhooks → D-03 breaker
        Tier 3: P-01 JWKS → P-02 packaging → P-03 CORS warning
```

**Why this order:** F-04 (eval harness) first so every subsequent experiment is measurable. MCP (F-05) next because it's the single highest-value frontier item. Redlock (D-01) before any work that leans harder on parallel `Send`. Tier 3 last — useful but not where the learning frontier is.

---

## 5. Acceptance Criteria for "Reversion Complete"

- [ ] `.agent/paul_graham_evaluator.md` and `.agent/pg_evaluation.md` deleted.
- [ ] Repo-wide search for go/no-go / VC / fund-decision language returns zero hits (outside the historical analysis doc).
- [ ] Root `README.md` exists and frames the project as a research playground with an explicit "is NOT a product" note.
- [ ] TSD §12 and `unknown_items.md` backlog reframed from customer/sales to learning objectives, with all technical content preserved.
- [ ] All existing tests still pass (`pytest`) — reframing is doc-only and must not touch behavior.
- [ ] Tier 1 backlog (frontier items) captured as tracked tickets/issues.

---

## 6. Risks & Notes

- **Risk: over-deletion.** The biggest mistake would be stripping legitimate engineering language or deleting the multi-tenant/identity *mechanics* along with the sales framing. Phase A2's "explicitly KEEP" list guards against this.
- **Risk: framing drift.** Without a README anchor (A3), future work re-introduces product language. The README is cheap insurance.
- **Note on the analysis doc:** `revert_to_core_spec_analysis.md` is a good record of *why* this happened — keep it. This plan is the *how*.
- **Note on scope discipline:** This is a playground. Prefer breadth of *concepts explored* over depth of *production hardening*. A working-but-rough reflection loop teaches more than a perfectly hardened webhook.
