import time
import logging
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from agentguard import (
    AgentGuardState, MemoryManager, Registry, RecursiveExecutor
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run_poc():
    logger.info("Initializing AgentGuard Framework...")

    # Setup Registry
    registry = Registry()
    registry.register(
        item_id="search_agent_01",
        role="Search Specialist",
        semantic_description="Can search the web for any topic.",
        input_schema={"query": "string"},
        output_schema={"results": "list"}
    )
    registry.register(
        item_id="analysis_agent_01",
        role="Data Analyst",
        semantic_description="Analyzes data and provides insights.",
        input_schema={"data": "string"},
        output_schema={"analysis": "string"}
    )

    # Setup Memory
    memory = MemoryManager()

    # Setup Executor (max depth 3 for the Fork Bomb PoC)
    executor = RecursiveExecutor(memory_manager=memory, registry=registry, max_depth=3)
    graph = executor.build_graph()

    logger.info("Starting 'Fork Bomb' Governance Stress Test...")

    # Initial State
    initial_state = AgentGuardState(
        task="Fork bomb recursive search and analyze",
        subject="Global Economics",
        root_task_id="poc_run_001",
        parent_node_id="root",
        depth=0,
        results={},
        all_agents=[],
        all_edges=[],
        global_signal="",
        usage_stats={"total_cost": 0.0},
        budget_config={"max_cost": 5.0}, # Restrict budget to force a break or see limit
        governance_decisions=[]
    )

    start_time = time.time()

    # Run the graph
    final_state = graph.invoke(initial_state)

    end_time = time.time()
    latency = end_time - start_time

    logger.info("--- TEST RESULTS ---")
    logger.info(f"Execution Latency: {latency:.4f} seconds")
    logger.info(f"Final Global Signal: {final_state.get('global_signal')}")
    logger.info(f"Total Cost Incurred: ${final_state.get('usage_stats', {}).get('total_cost', 0):.2f}")

    decisions = final_state.get('governance_decisions', [])
    logger.info(f"Total Governance Decisions (Root Level): {len(decisions)}")

    final_answer = final_state.get('results', {}).get('final_answer')
    logger.info(f"Final Answer Preview: {str(final_answer)[:200]}...")

if __name__ == "__main__":
    run_poc()
