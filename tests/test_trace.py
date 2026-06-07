"""
tests/test_trace.py — Unit tests for agentguard.trace

Coverage:
  - new_event() produces correct structure
  - dump() linearizes events, builds edges, handles empty runs
  - Parent/child causality is preserved for recursive graphs
  - Redactor hook is applied to all events
  - write_to_disk() creates valid JSON file
"""
import json
import os
import tempfile
import pytest

import agentguard.trace as tracer


# --------------------------------------------------------------------------- #
#  Fixtures                                                                    #
# --------------------------------------------------------------------------- #

def _minimal_state(**overrides):
    base = {
        "root_task_id": "test_run_001",
        "task": "Analyze renewable energy trends",
        "usage_stats": {"total_cost": 0.12},
        "results": {"final_answer": "Solar capacity grew 40% YoY."},
        "trace_events": [],
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
#  new_event                                                                   #
# --------------------------------------------------------------------------- #

class TestNewEvent:
    def test_required_fields_present(self):
        ev = tracer.new_event(run_id="r1", node="preflight", depth=0)
        assert ev["event_id"]
        assert ev["run_id"] == "r1"
        assert ev["node"] == "preflight"
        assert ev["depth"] == 0
        assert ev["timestamp"]
        assert ev["status"] == "ok"

    def test_optional_fields_default_none(self):
        ev = tracer.new_event(run_id="r1", node="preflight", depth=0)
        assert ev["parent_event_id"] is None
        assert ev["agent_id"] is None
        assert ev["action"] is None
        assert ev["duration_ms"] is None
        assert ev["decision"] is None

    def test_decision_serialized_as_dict(self):
        decision = {"action": "start_task", "allowed": True, "reason": "OK", "cost": 0.0, "hitl_required": False}
        ev = tracer.new_event(run_id="r1", node="preflight", depth=0, decision=decision)
        assert ev["decision"] == decision

    def test_unique_event_ids(self):
        ids = {tracer.new_event(run_id="r1", node="preflight", depth=0)["event_id"] for _ in range(50)}
        assert len(ids) == 50

    def test_metadata_defaults_to_empty_dict(self):
        ev = tracer.new_event(run_id="r1", node="preflight", depth=0)
        assert ev["metadata"] == {}

    def test_custom_metadata(self):
        ev = tracer.new_event(run_id="r1", node="decompose", depth=0, metadata={"subtasks": ["a", "b"]})
        assert ev["metadata"]["subtasks"] == ["a", "b"]

    def test_llm_token_counts_stored(self):
        counts = {"prompt_tokens": 512, "completion_tokens": 128, "total_tokens": 640}
        ev = tracer.new_event(run_id="r1", node="synthesize", depth=0, llm_token_counts=counts)
        assert ev["llm_token_counts"] == counts

    def test_latency_breakdown_stored(self):
        breakdown = {"llm_ms": 850, "policy_ms": 40, "overhead_ms": 12}
        ev = tracer.new_event(run_id="r1", node="synthesize", depth=0, latency_breakdown=breakdown)
        assert ev["latency_breakdown"] == breakdown

    def test_new_fields_default_none(self):
        ev = tracer.new_event(run_id="r1", node="preflight", depth=0)
        assert ev["llm_token_counts"] is None
        assert ev["latency_breakdown"] is None


# --------------------------------------------------------------------------- #
#  dump                                                                        #
# --------------------------------------------------------------------------- #

class TestDump:
    def test_empty_run_produces_valid_schema(self):
        state = _minimal_state()
        doc = tracer.dump(state, started_at="2026-04-10T14:00:00+00:00")
        assert doc["schema_version"] == "1.1"
        assert doc["run_id"] == "test_run_001"
        assert doc["events"] == []
        assert doc["edges"] == []
        assert doc["total_cost_usd"] == 0.12

    def test_final_answer_included(self):
        state = _minimal_state()
        doc = tracer.dump(state)
        assert doc["final_answer"] == "Solar capacity grew 40% YoY."

    def test_edges_built_from_parent_links(self):
        ev1 = tracer.new_event(run_id="r1", node="preflight", depth=0)
        ev2 = tracer.new_event(run_id="r1", node="decompose", depth=0, parent_event_id=ev1["event_id"])
        ev3 = tracer.new_event(run_id="r1", node="plan", depth=0, parent_event_id=ev2["event_id"])

        state = _minimal_state(trace_events=[ev1, ev2, ev3])
        doc = tracer.dump(state)

        assert len(doc["edges"]) == 2
        edge_map = {e["from"]: e["to"] for e in doc["edges"]}
        assert edge_map[ev1["event_id"]] == ev2["event_id"]
        assert edge_map[ev2["event_id"]] == ev3["event_id"]

    def test_root_event_has_no_edge(self):
        ev = tracer.new_event(run_id="r1", node="preflight", depth=0)
        state = _minimal_state(trace_events=[ev])
        doc = tracer.dump(state)
        assert doc["edges"] == []

    def test_duration_computed_when_timestamps_provided(self):
        state = _minimal_state()
        doc = tracer.dump(
            state,
            started_at="2026-04-10T14:00:00+00:00",
            finished_at="2026-04-10T14:00:37+00:00",
        )
        assert doc["total_duration_ms"] == 37_000

    def test_duration_none_when_no_timestamps(self):
        state = _minimal_state()
        doc = tracer.dump(state)
        assert doc["total_duration_ms"] is None

    def test_redactor_applied_to_all_events(self):
        events = [tracer.new_event(run_id="r1", node="preflight", depth=0) for _ in range(3)]
        state = _minimal_state(trace_events=events)

        def scrub(ev):
            ev = dict(ev)
            ev["intent"] = "[REDACTED]"
            return ev

        doc = tracer.dump(state, redactor=scrub)
        for ev in doc["events"]:
            assert ev["intent"] == "[REDACTED]"

    def test_child_graph_causality(self):
        """Parent execute_subtasks event ← child preflight event chain."""
        root_exec = tracer.new_event(run_id="r1", node="execute_subtasks", depth=0)
        child_preflight = tracer.new_event(
            run_id="r1", node="preflight", depth=1,
            parent_event_id=root_exec["event_id"],
        )
        child_exec = tracer.new_event(
            run_id="r1", node="execute_subtasks", depth=1,
            parent_event_id=child_preflight["event_id"],
        )

        state = _minimal_state(trace_events=[root_exec, child_preflight, child_exec])
        doc = tracer.dump(state)

        edge_map = {e["from"]: e["to"] for e in doc["edges"]}
        assert edge_map[root_exec["event_id"]] == child_preflight["event_id"]
        assert edge_map[child_preflight["event_id"]] == child_exec["event_id"]

    def test_final_status_propagated(self):
        state = _minimal_state()
        doc = tracer.dump(state, final_status="blocked")
        assert doc["final_status"] == "blocked"

    def test_llm_providers_propagated(self):
        state = _minimal_state()
        providers = {"chat": "google:gemini-2.0-flash", "embeddings": "openai:text-embedding-3-small"}
        doc = tracer.dump(state, llm_providers=providers)
        assert doc["llm_providers"] == providers


# --------------------------------------------------------------------------- #
#  write_to_disk                                                               #
# --------------------------------------------------------------------------- #

class TestWriteToDisk:
    def test_writes_valid_json(self, tmp_path):
        state = _minimal_state()
        doc = tracer.dump(state)
        path = tracer.write_to_disk(doc, directory=str(tmp_path))
        assert os.path.exists(path)
        with open(path) as f:
            loaded = json.load(f)
        assert loaded["run_id"] == "test_run_001"

    def test_filename_uses_run_id(self, tmp_path):
        state = _minimal_state()
        doc = tracer.dump(state)
        path = tracer.write_to_disk(doc, directory=str(tmp_path))
        assert os.path.basename(path) == "test_run_001.json"

    def test_creates_directory_if_missing(self, tmp_path):
        new_dir = str(tmp_path / "nested" / "traces")
        state = _minimal_state()
        doc = tracer.dump(state)
        tracer.write_to_disk(doc, directory=new_dir)
        assert os.path.isdir(new_dir)

    def test_round_trip_preserves_events(self, tmp_path):
        ev1 = tracer.new_event(run_id="test_run_001", node="preflight", depth=0)
        ev2 = tracer.new_event(
            run_id="test_run_001", node="decompose", depth=0,
            parent_event_id=ev1["event_id"],
        )
        state = _minimal_state(trace_events=[ev1, ev2])
        doc = tracer.dump(state)
        path = tracer.write_to_disk(doc, directory=str(tmp_path))
        with open(path) as f:
            loaded = json.load(f)
        assert len(loaded["events"]) == 2
        assert len(loaded["edges"]) == 1
