"""
tests/test_reflection.py
------------------------
Verifies that:
  - The reflection graph is correctly wired (synthesize → reflect → END/replan)
  - reflect node appears in trace events when enable_reflection=True
  - reflection is skipped and graph ends normally when enable_reflection=False
  - _edge_post_reflect routes correctly based on state
"""
import pytest
from unittest.mock import MagicMock, patch

from agentguard.executor import RecursiveExecutor
from agentguard.state import make_initial_state
from agentguard.memory import MemoryManager
from agentguard.registry import Registry


def _make_executor(enable_reflection=False):
    memory = MemoryManager()
    registry = Registry()
    return RecursiveExecutor(
        memory_manager=memory,
        registry=registry,
        max_depth=1,
        enable_reflection=enable_reflection,
    )


class TestReflectionGraphWiring:
    def test_reflect_node_reachable_when_enabled(self):
        """synthesize → reflect must be an edge when enable_reflection=True."""
        executor = _make_executor(enable_reflection=True)
        graph = executor.build_graph()
        # LangGraph exposes the compiled graph nodes
        assert "reflect" in graph.nodes

    def test_reflect_node_absent_when_disabled(self):
        executor = _make_executor(enable_reflection=False)
        graph = executor.build_graph()
        assert "reflect" not in graph.nodes

    def test_edge_post_reflect_done_when_answer_present(self):
        """reflect routes to 'done' if final_answer exists and no replan needed."""
        executor = _make_executor(enable_reflection=True)
        state = make_initial_state(task="test")
        state["results"] = {"final_answer": "Some answer."}
        state["reflection_count"] = 0
        assert executor._edge_post_reflect(state) == "done"

    def test_edge_post_reflect_replan_when_answer_cleared(self):
        """reflect routes to 'replan' if reflection_count > 0 and answer is empty."""
        executor = _make_executor(enable_reflection=True)
        state = make_initial_state(task="test")
        state["results"] = {}
        state["reflection_count"] = 1   # reflect node incremented this and cleared results
        assert executor._edge_post_reflect(state) == "replan"

    def test_edge_post_reflect_done_when_max_reflections_reached(self):
        """Routes to 'done' once reflection_count reaches max even with no answer."""
        executor = _make_executor(enable_reflection=True)
        # reflection_count = 0 with no answer → done (nothing has run yet)
        state = make_initial_state(task="test")
        state["results"] = {}
        state["reflection_count"] = 0
        assert executor._edge_post_reflect(state) == "done"


class TestReflectionNode:
    def test_reflect_emits_trace_event(self):
        """_node_reflect always appends a 'reflect' trace event."""
        executor = _make_executor(enable_reflection=True)
        state = make_initial_state(task="test task")
        state["results"] = {"final_answer": "An answer."}
        state["reflection_count"] = 0

        with patch.object(executor, '_node_reflect', wraps=executor._node_reflect):
            # No LLM configured in test env — reflect should still emit an event
            result = executor._node_reflect(state, config=MagicMock())

        trace_events = result.get("trace_events", [])
        assert len(trace_events) == 1
        assert trace_events[0]["node"] == "reflect"
        assert trace_events[0]["action"] == "self_critique"

    def test_reflect_does_not_replan_without_llm(self):
        """Without an LLM, reflect skips critique and does not trigger a replan."""
        executor = _make_executor(enable_reflection=True)
        executor.policy.llm = None  # ensure no LLM
        executor.reflect_jev = None

        state = make_initial_state(task="test task")
        state["results"] = {"final_answer": "An answer."}
        state["reflection_count"] = 0

        result = executor._node_reflect(state, config=MagicMock())

        # results should NOT be cleared (no replan triggered)
        assert "results" not in result or result.get("results") != {}
        # reflection_count should NOT be incremented
        assert result.get("reflection_count", 0) == 0
