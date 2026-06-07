"""
run_eval.py — CLI entry point for the AgentGuard evaluation harness.

Runs the built-in scenario set (governance ON vs OFF) and prints a report
showing whether policy enforcement changed behavior, cost, or safety outcomes.

Usage:
    uv run python scripts/run_eval.py

Optional env vars:
    POLICY_FILE        path to the policy.yaml to test (default: policy.yaml)
    AGENTGUARD_NO_AUTH set to 1 to bypass JWT checks during eval

Add custom scenarios by importing EvalScenario and passing them to harness.run().
"""
import sys
import os
import logging

# Allow running from repo root without installing the package
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

logging.basicConfig(
    level=logging.WARNING,   # suppress executor noise; eval report is the output
    format="%(levelname)s | %(name)s | %(message)s",
)

from agentguard.eval import EvalHarness, BUILT_IN_SCENARIOS
from agentguard.memory import MemoryManager
from agentguard.registry import Registry


def main():
    os.environ.setdefault("AGENTGUARD_NO_AUTH", "1")

    registry = Registry()
    memory = MemoryManager()
    harness = EvalHarness(registry=registry, memory_manager=memory, max_depth=1)

    results = harness.run(BUILT_IN_SCENARIOS)
    harness.print_report(results)

    failed = [r for r in results if not r.passed]
    if failed:
        print(f"  {len(failed)} scenario(s) did not match expected status — see report above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
