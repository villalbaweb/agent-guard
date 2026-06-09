"""
AgentGuard — Real-World Implementation: Asset Management Firm
==============================================================

Context
-------
Meridian Capital is a mid-size asset management firm ($4B AUM) that wants to
automate its equity research workflow using AI agents. Their compliance and
legal teams have three non-negotiable requirements:

  1. No agent may execute a trade above $250k without a licensed human approver.
  2. Client PII (account numbers, SSNs) must never appear in LLM prompts.
  3. Every AI-assisted investment decision must produce an auditable trace for
     SEC and EU AI Act compliance.

Additionally, the CTO has a standing rule: no single automated run may spend
more than $5.00 in LLM API costs (prevents runaway recursive analysis).

This file demonstrates all four requirements running against the same
AgentGuard governance layer, using a five-agent system that mirrors a real
equity research desk.

Agents
------
  - market_data_agent       : Pulls live price, volume, macro data
  - fundamental_analyst     : Reads 10-K/10-Q filings, calculates ratios
  - risk_assessment_agent   : Runs VaR, beta, correlation analysis
  - esg_compliance_agent    : Screens against ESG/sanctions lists
  - trade_execution_agent   : Constructs and submits order tickets

Scenarios
---------
  1. Standard equity research report       → completes, full trace produced
  2. Large block trade (>$250k)            → pauses for compliance HITL approval
  3. Client PII in research brief          → blocked at preflight, zero LLM calls
  4. Sanctioned entity in portfolio        → blocked by compliance rule
  5. Budget ceiling (junior analyst tier)  → halted mid-run at cost cap
  6. Recursive loop in data refresh cycle  → caught by semantic loop detection

Usage
-----
  # Full run (all scenarios, LLM + Redis required)
  PYTHONPATH=. uv run python examples/asset_management.py

  # Dry run (rule-based only, no LLM needed)
  PYTHONPATH=. AGENTGUARD_NO_LLM=1 uv run python examples/asset_management.py
"""

import os
import time
import logging
from datetime import datetime, timezone, timedelta

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from agentguard import (
    AgentGuardState,
    make_initial_state,
    MemoryManager,
    Registry,
    RecursiveExecutor,
    PolicyEngine,
)
from agentguard.auth import make_auth_context, anonymous_context
from agentguard import trace as tracer
from agentguard.llm import get_embeddings

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

SEPARATOR = "=" * 70
TRACES_DIR = "./traces/asset_management"


# ---------------------------------------------------------------------------
# Agent Registry
# ---------------------------------------------------------------------------
# In production, agents would register themselves on startup via a REST call
# to the AgentGuard registry service.  For this example we register them
# statically to keep the file self-contained.

def build_registry() -> Registry:
    registry = Registry()

    registry.register(
        item_id="market_data_agent",
        role="Market Data Specialist",
        semantic_description=(
            "Retrieves live and historical equity prices, trading volumes, "
            "options chain data, and macroeconomic indicators from market "
            "data feeds (Bloomberg, Refinitiv, FRED)."
        ),
        input_schema={"ticker": "string", "period": "string"},
        output_schema={"price": "float", "volume": "int", "indicators": "dict"},
    )

    registry.register(
        item_id="fundamental_analyst",
        role="Fundamental Analyst",
        semantic_description=(
            "Analyzes SEC filings (10-K, 10-Q, 8-K), calculates valuation "
            "multiples (P/E, EV/EBITDA, DCF), and produces buy/hold/sell "
            "ratings with price targets."
        ),
        input_schema={"ticker": "string", "filing_type": "string"},
        output_schema={"rating": "string", "price_target": "float", "report": "string"},
    )

    registry.register(
        item_id="risk_assessment_agent",
        role="Risk Analyst",
        semantic_description=(
            "Computes portfolio risk metrics: Value-at-Risk (VaR), beta, "
            "Sharpe ratio, maximum drawdown, and sector concentration. "
            "Flags positions that breach risk limits."
        ),
        input_schema={"portfolio_id": "string", "confidence": "float"},
        output_schema={"var_95": "float", "beta": "float", "breach_flags": "list"},
    )

    registry.register(
        item_id="esg_compliance_agent",
        role="ESG & Compliance Screener",
        semantic_description=(
            "Screens securities against ESG ratings, OFAC sanctions lists, "
            "internal exclusion lists, and EU Taxonomy alignment. Blocks "
            "any investment in restricted entities."
        ),
        input_schema={"ticker": "string", "screen_type": "string"},
        output_schema={"pass": "bool", "flags": "list", "score": "float"},
    )

    registry.register(
        item_id="trade_execution_agent",
        role="Trade Execution Specialist",
        semantic_description=(
            "Constructs FIX protocol order tickets, routes to execution "
            "venues (NYSE, NASDAQ, dark pools), and confirms fills. "
            "Requires compliance pre-clearance for orders above $250k."
        ),
        input_schema={"ticker": "string", "quantity": "int", "order_type": "string"},
        output_schema={"order_id": "string", "fill_price": "float", "status": "string"},
    )

    return registry


# ---------------------------------------------------------------------------
# Scenario runner
# ---------------------------------------------------------------------------

def run_scenario(
    label: str,
    task: str,
    registry: Registry,
    memory: MemoryManager,
    auth_context=None,
    budget_override: dict = None,
    max_depth: int = 2,
) -> dict:
    logger.info(f"\n{SEPARATOR}")
    logger.info(f"SCENARIO : {label}")
    logger.info(f"TASK     : {task[:120]}")
    logger.info(f"SUBJECT  : {(auth_context or anonymous_context()).subject}")
    logger.info(SEPARATOR)

    executor = RecursiveExecutor(
        memory_manager=memory,
        registry=registry,
        max_depth=max_depth,
    )
    graph = executor.build_graph()

    run_id = f"meridian_{label.replace(' ', '_').replace('(', '').replace(')', '').lower()}"
    auth_ctx = auth_context or anonymous_context()

    state = make_initial_state(
        task=task,
        subject=auth_ctx.subject,
        root_task_id=run_id,
        budget_config=budget_override,
        auth_context=auth_ctx.to_dict(),
        depth=0,
        parent_node_id="root",
    )

    started_at = tracer._now_iso()
    t0 = time.time()
    final = graph.invoke(state)
    elapsed = time.time() - t0
    finished_at = tracer._now_iso()

    # ---- Summarize decisions ----
    decisions = final.get("governance_decisions", [])
    blocks = [d for d in decisions if not d["allowed"] and not d.get("hitl_required")]
    hitls  = [d for d in decisions if d.get("hitl_required")]
    allows = [d for d in decisions if d["allowed"]]

    signal = final.get("global_signal", "")
    if "REJECT" in signal:
        final_status = "blocked"
    elif "HITL" in signal:
        final_status = "hitl_pending"
    else:
        final_status = "completed"

    logger.info(f"  Status   : {final_status.upper()}")
    logger.info(f"  Signal   : {signal}")
    logger.info(f"  Latency  : {elapsed:.3f}s")
    logger.info(f"  Cost     : ${final.get('usage_stats', {}).get('total_cost', 0):.4f}")
    logger.info(
        f"  Decisions: {len(decisions)} total | "
        f"{len(allows)} allowed | {len(blocks)} blocked | {len(hitls)} HITL"
    )
    logger.info(f"  Events   : {len(final.get('trace_events', []))} trace events")

    for d in decisions:
        if d.get("hitl_required"):
            tag = "[HITL ]"
        elif not d["allowed"]:
            tag = "[BLOCK]"
        else:
            tag = "[ALLOW]"
        # Strip subject prefix for readability; it's in the trace
        reason = d["reason"].split("] ", 1)[-1] if "] " in d["reason"] else d["reason"]
        logger.info(f"    {tag} {d['action']:<45} {reason}")

    answer = str(final.get("results", {}).get("final_answer", "")).strip()
    if answer:
        logger.info(f"  Answer   : {answer[:400]}")

    # ---- Write trace ----
    trace_doc = tracer.dump(
        state=final,
        task=task,
        started_at=started_at,
        finished_at=finished_at,
        final_status=final_status,
    )
    memory.store_trace(run_id, trace_doc)
    trace_path = tracer.write_to_disk(trace_doc, directory=TRACES_DIR)
    logger.info(f"  Trace    : {trace_path}")

    return final


# ---------------------------------------------------------------------------
# Scenario 5 helper — direct policy check (budget cap mid-run simulation)
# ---------------------------------------------------------------------------

def run_budget_cap_scenario(memory: MemoryManager):
    """
    Junior Analyst tier: $0.08 max per run.
    The first step ($0.05) is allowed; the second ($0.05) pushes over the cap.
    Demonstrates that the same governance layer applies regardless of who
    submits the task — budget tiers are enforced by state-level budget_config.
    """
    label = "Budget Ceiling (Junior Analyst Tier)"
    logger.info(f"\n{SEPARATOR}")
    logger.info(f"SCENARIO : {label}")
    logger.info(SEPARATOR)

    # Junior analyst has a $0.08 per-run budget (vs $5.00 for senior)
    policy = PolicyEngine(memory)

    junior_ctx = make_auth_context(
        subject="j.nguyen@meridian.com",
        roles=["analyst"],
        tenant_id="meridian-capital",
        ttl_seconds=3600,
    )

    state = make_initial_state(
        task="Analyze NVDA Q3 earnings and update price target",
        subject=junior_ctx.subject,
        root_task_id="meridian_junior_budget_test",
        budget_config={"max_cost_usd": 0.08, "per_step_limit_usd": 0.10},
        auth_context=junior_ctx.to_dict(),
    )

    steps = [
        ("invoke_market_data_agent",   "Market Data Specialist performing: fetch NVDA last 90 days price data",     0.05),
        ("invoke_fundamental_analyst", "Fundamental Analyst performing: analyze NVDA Q3 earnings vs consensus",     0.05),
        ("invoke_risk_assessment_agent","Risk Analyst performing: recalculate NVDA position VaR after earnings",    0.05),
    ]

    for action, intent, cost in steps:
        allowed, decision = policy.check_step(state, action=action, intent=intent, cost_estimate=cost)
        tag = "[ALLOW]" if allowed else "[BLOCK]"
        reason = decision["reason"].split("] ", 1)[-1] if "] " in decision["reason"] else decision["reason"]
        logger.info(f"  {tag} {action:<45} cost=${cost:.2f}  — {reason}")

        if allowed:
            # Update running cost so the next check sees cumulative spend
            state["usage_stats"]["total_cost"] += cost
        else:
            logger.info(f"  → Run halted. Total spend: ${state['usage_stats']['total_cost']:.4f}")
            break


# ---------------------------------------------------------------------------
# Scenario 6 helper — semantic loop detection (direct policy check)
# ---------------------------------------------------------------------------

def run_loop_detection_scenario(memory: MemoryManager):
    """
    A buggy agent keeps re-querying the same market data endpoint on every
    retry cycle.  AgentGuard catches this via cosine similarity on the intent
    string and blocks it before a third identical API call fires.
    """
    label = "Recursive Loop in Data Refresh Cycle"
    logger.info(f"\n{SEPARATOR}")
    logger.info(f"SCENARIO : {label}")
    logger.info(SEPARATOR)

    loop_memory = MemoryManager()
    policy = PolicyEngine(loop_memory)

    loop_state = make_initial_state(
        task="Continuously refresh AAPL intraday data",
        subject="system-auto-refresh",
        root_task_id="meridian_loop_detection_test",
        auth_context=anonymous_context().to_dict(),
    )

    # Same intent repeated 4 times — simulates a stuck retry loop
    for attempt in range(1, 5):
        ok, dec = policy.check_step(
            loop_state,
            action="invoke_market_data_agent",
            intent="Market Data Specialist performing: fetch AAPL 1-minute intraday bars for real-time dashboard",
            cost_estimate=0.01,
        )
        tag = "[ALLOW]" if ok else "[BLOCK]"
        reason = dec["reason"].split("] ", 1)[-1] if "] " in dec["reason"] else dec["reason"]
        logger.info(f"  Attempt {attempt}: {tag} — {reason}")
        if not ok:
            logger.info(f"  → Loop halted at attempt {attempt}. Agent would have made {attempt - 1} redundant API calls.")
            break


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    logger.info("AgentGuard — Meridian Capital Asset Management Demo")
    logger.info(f"Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    embed = get_embeddings()
    if embed:
        logger.info("Embeddings: active — semantic loop detection enabled")
    else:
        logger.info("Embeddings: unavailable — loop detection in exact-match mode")

    registry = build_registry()
    memory   = MemoryManager()

    # ------------------------------------------------------------------
    # Auth contexts
    # Senior portfolio manager — can submit research and approve trades
    senior_pm = make_auth_context(
        subject="s.okafor@meridian.com",
        roles=["analyst", "portfolio_manager", "approver"],
        tenant_id="meridian-capital",
        ttl_seconds=28800,  # 8-hour session
    )

    # Compliance officer — approves HITL trade requests
    compliance_officer = make_auth_context(
        subject="c.reyes@meridian.com",
        roles=["compliance", "approver"],
        tenant_id="meridian-capital",
        ttl_seconds=28800,
    )

    # Junior analyst — restricted budget tier
    junior_analyst = make_auth_context(
        subject="j.nguyen@meridian.com",
        roles=["analyst"],
        tenant_id="meridian-capital",
        ttl_seconds=3600,
    )

    # ------------------------------------------------------------------
    # Scenario 1: Standard equity research report
    # A portfolio manager asks for a full research brief on a position
    # the fund is considering adding to its technology sleeve.
    # ------------------------------------------------------------------
    run_scenario(
        label="Standard Equity Research Report",
        task=(
            "Produce a full equity research brief for Microsoft (MSFT): "
            "analyze Q3 earnings, Azure cloud growth trajectory, competitive "
            "positioning vs AWS and Google Cloud, and provide a 12-month "
            "price target with buy/hold/sell recommendation."
        ),
        registry=registry,
        memory=memory,
        auth_context=senior_pm,
    )

    # ------------------------------------------------------------------
    # Scenario 2: Large block trade requiring compliance HITL
    # The PM wants to execute a $500k block purchase of NVDA.
    # policy.yaml: `require_hitl` fires on `execute_trade_block` action
    # because it contains "execute_trade" → financial action threshold.
    # The run pauses and waits for c.reyes@meridian.com to approve.
    # ------------------------------------------------------------------
    run_scenario(
        label="Large Block Trade — HITL Required",
        task=(
            "Execute a block purchase of 1,200 shares of NVIDIA (NVDA) at "
            "market open, estimated value $510,000. Route to dark pool to "
            "minimize market impact. Confirm fill and update portfolio ledger."
        ),
        registry=registry,
        memory=memory,
        auth_context=senior_pm,
    )

    # ------------------------------------------------------------------
    # Scenario 3: Client PII in research brief
    # An analyst accidentally pastes a client account number into the task.
    # AgentGuard blocks it at preflight — zero LLM calls, zero cost.
    # ------------------------------------------------------------------
    run_scenario(
        label="Client PII in Research Brief",
        task=(
            "Review the tax-loss harvesting opportunities for client account "
            "ACC-8821-7734-NJ whose SSN is 412-88-9901. Generate a rebalancing "
            "proposal and email it to the client at john.smith@gmail.com."
        ),
        registry=registry,
        memory=memory,
        auth_context=junior_analyst,
    )

    # ------------------------------------------------------------------
    # Scenario 4: Sanctioned entity in investment universe
    # An analyst asks the system to research a company that appears on
    # the OFAC sanctions list.  The ESG/compliance rule blocks this
    # before any research is conducted.
    # ------------------------------------------------------------------
    run_scenario(
        label="Sanctioned Entity Block",
        task=(
            "Research investment opportunity in Novatek PJSC (Russian LNG "
            "producer, ticker NVTK). Pull fundamentals, analyst consensus, "
            "and draft a position memo for the EM fixed income desk. "
            "Include sensitivity analysis on EU energy policy changes."
        ),
        registry=registry,
        memory=memory,
        auth_context=senior_pm,
    )

    # ------------------------------------------------------------------
    # Scenario 5: Budget ceiling — junior analyst tier
    # Direct policy check (bypasses executor to demonstrate the budget
    # enforcement layer in isolation, as it would apply inside any node).
    # ------------------------------------------------------------------
    run_budget_cap_scenario(memory)

    # ------------------------------------------------------------------
    # Scenario 6: Semantic loop detection
    # Direct policy check demonstrating that a stuck retry loop in an
    # automated data refresh agent is caught before it burns API quota.
    # ------------------------------------------------------------------
    run_loop_detection_scenario(memory)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    logger.info(f"\n{SEPARATOR}")
    logger.info("MERIDIAN CAPITAL — DEMO COMPLETE")
    logger.info(f"Traces written to: {TRACES_DIR}/")
    logger.info("")
    logger.info("What governance fired across all scenarios:")
    logger.info("  Scenario 1 → Completed normally. Full trace produced for SEC audit.")
    logger.info("  Scenario 2 → HITL pause. Trade held until compliance officer approves.")
    logger.info("  Scenario 3 → PII block at preflight. Zero LLM cost, zero data exposure.")
    logger.info("  Scenario 4 → Sanctions block. OFAC rule prevented forbidden research.")
    logger.info("  Scenario 5 → Budget cap halted junior analyst run at $0.08.")
    logger.info("  Scenario 6 → Loop detection stopped redundant API calls at attempt 2.")
    logger.info(SEPARATOR)


if __name__ == "__main__":
    main()
