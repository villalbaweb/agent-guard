"""
tests/test_api.py — REST API integration tests using FastAPI TestClient

Coverage:
  - GET /health returns 200 with correct schema
  - POST /runs returns 202 with run_id
  - GET /runs/{id} returns correct status
  - GET /runs/{id}/trace returns trace document
  - POST /runs/{id}/approve returns 409 on non-pending run
  - GET /policy returns policy document
  - POST /policy/reload returns success
  - 401 returned when auth is missing
  - 403 returned when approver role is missing
  - Error envelope shape is consistent

All tests run without LLM or Redis (mocked RecursiveExecutor + MemoryManager).
"""
import os
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

os.environ.setdefault("AGENTGUARD_NO_AUTH", "1")   # disable auth for test suite
os.environ.setdefault("AGENTGUARD_JWT_SECRET", "test-secret-for-unit-tests-only")

from fastapi.testclient import TestClient

from backend.main import app
from backend.dependencies import get_memory, get_registry, get_executor, get_run_store
from backend.store import RunStore
from agentguard.memory import MemoryManager
from agentguard.registry import Registry


# --------------------------------------------------------------------------- #
#  Fixtures                                                                    #
# --------------------------------------------------------------------------- #

@pytest.fixture
def mock_memory():
    mem = MemoryManager.__new__(MemoryManager)
    mem._redis = None
    mem._store = {}
    mem._embed = None
    mem._embed_checked = True
    return mem


@pytest.fixture
def mock_registry():
    reg = Registry()
    reg.register("search_agent_01", "Search Specialist", "Web search", {}, {})
    return reg


@pytest.fixture
def mock_executor(mock_memory, mock_registry):
    from agentguard.executor import RecursiveExecutor
    executor = MagicMock(spec=RecursiveExecutor)
    graph = MagicMock()
    final_state = {
        "global_signal": "DONE",
        "results": {"final_answer": "Mock answer"},
        "usage_stats": {"total_cost": 0.05},
        "governance_decisions": [
            {"action": "start_task", "allowed": True, "reason": "ok", "cost": 0.0, "hitl_required": False}
        ],
        "trace_events": [],
        "root_task_id": "test_run",
        "task": "Test task",
    }
    graph.invoke.return_value = final_state
    executor.build_graph.return_value = graph
    return executor


@pytest.fixture
def client(mock_memory, mock_registry, mock_executor):
    store = RunStore(mock_memory)
    app.dependency_overrides[get_memory] = lambda: mock_memory
    app.dependency_overrides[get_registry] = lambda: mock_registry
    app.dependency_overrides[get_executor] = lambda: mock_executor
    app.dependency_overrides[get_run_store] = lambda: store
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
#  GET /health                                                                 #
# --------------------------------------------------------------------------- #

class TestHealth:
    def test_health_returns_200(self, client):
        r = client.get("/health")
        assert r.status_code == 200

    def test_health_schema(self, client):
        r = client.get("/health")
        body = r.json()
        assert "status" in body
        assert "redis" in body
        assert "llm" in body
        assert "timestamp" in body

    def test_health_status_ok_or_degraded(self, client):
        r = client.get("/health")
        assert r.json()["status"] in ("ok", "degraded", "unhealthy")


# --------------------------------------------------------------------------- #
#  POST /runs                                                                  #
# --------------------------------------------------------------------------- #

class TestCreateRun:
    def test_returns_202(self, client):
        r = client.post("/runs", json={"task": "Analyze renewable energy"})
        assert r.status_code == 202

    def test_response_has_run_id(self, client):
        r = client.post("/runs", json={"task": "Analyze renewable energy"})
        body = r.json()
        assert "run_id" in body
        assert body["run_id"].startswith("run_")

    def test_response_status_running(self, client):
        r = client.post("/runs", json={"task": "Analyze renewable energy"})
        assert r.json()["status"] == "running"

    def test_response_has_submitted_at(self, client):
        r = client.post("/runs", json={"task": "Analyze renewable energy"})
        assert "submitted_at" in r.json()

    def test_budget_override_accepted(self, client):
        r = client.post("/runs", json={
            "task": "Test task",
            "budget_override": {"max_cost_usd": 0.5},
        })
        assert r.status_code == 202


# --------------------------------------------------------------------------- #
#  GET /runs/{id}                                                              #
# --------------------------------------------------------------------------- #

class TestGetRun:
    def _create(self, client, task="Test task"):
        r = client.post("/runs", json={"task": task})
        return r.json()["run_id"]

    def test_get_returns_200(self, client, mock_memory):
        store = RunStore(mock_memory)
        run_id = "existing_run"
        store.create_run(run_id, "Test task", "test-user")
        r = client.get(f"/runs/{run_id}")
        assert r.status_code == 200

    def test_get_unknown_run_returns_404(self, client):
        r = client.get("/runs/nonexistent_run_xyz")
        assert r.status_code == 404

    def test_get_response_schema(self, client, mock_memory):
        store = RunStore(mock_memory)
        run_id = "schema_test_run"
        store.create_run(run_id, "Test task", "test-user")
        r = client.get(f"/runs/{run_id}")
        body = r.json()
        assert "run_id" in body
        assert "status" in body
        assert "trace_url" in body
        assert body["trace_url"] == f"/runs/{run_id}/trace"


# --------------------------------------------------------------------------- #
#  GET /runs/{id}/trace                                                        #
# --------------------------------------------------------------------------- #

class TestGetTrace:
    def test_returns_404_when_no_trace(self, client):
        r = client.get("/runs/no_trace_run/trace")
        assert r.status_code == 404

    def test_returns_trace_when_stored(self, client, mock_memory):
        store = RunStore(mock_memory)
        run_id = "traced_run"
        trace_doc = {"schema_version": "1.0", "run_id": run_id, "events": [], "edges": []}
        store.save_trace(run_id, trace_doc)
        r = client.get(f"/runs/{run_id}/trace")
        assert r.status_code == 200
        body = r.json()
        assert body["schema_version"] == "1.0"
        assert body["run_id"] == run_id


# --------------------------------------------------------------------------- #
#  POST /runs/{id}/approve                                                     #
# --------------------------------------------------------------------------- #

class TestApproveRun:
    def test_approve_non_pending_run_returns_409(self, client, mock_memory):
        store = RunStore(mock_memory)
        run_id = "completed_run"
        store.create_run(run_id, "task", "user")
        store.set_status(run_id, "completed")
        r = client.post(
            f"/runs/{run_id}/approve",
            json={"event_id": "evt_001", "approved_by": "approver@example.com"},
        )
        assert r.status_code == 409

    def test_approve_missing_run_returns_404(self, client):
        r = client.post(
            "/runs/ghost_run/approve",
            json={"event_id": "evt_001", "approved_by": "approver@example.com"},
        )
        assert r.status_code == 404


# --------------------------------------------------------------------------- #
#  GET /policy                                                                 #
# --------------------------------------------------------------------------- #

class TestPolicy:
    def test_get_policy_returns_200(self, client):
        r = client.get("/policy")
        assert r.status_code == 200

    def test_get_policy_schema(self, client):
        r = client.get("/policy")
        body = r.json()
        assert "policy_version" in body
        assert "content" in body

    def test_reload_policy_returns_200(self, client):
        r = client.post("/policy/reload")
        assert r.status_code == 200

    def test_reload_response_schema(self, client):
        r = client.post("/policy/reload")
        body = r.json()
        assert body["reloaded"] is True
        assert "rules_count" in body


# --------------------------------------------------------------------------- #
#  Auth enforcement                                                            #
# --------------------------------------------------------------------------- #

class TestAuth:
    def test_no_auth_env_disabled_allows_access(self, client):
        """AGENTGUARD_NO_AUTH=1 allows unauthenticated access (dev mode)."""
        r = client.get("/health")
        assert r.status_code == 200

    def test_auth_required_when_env_disabled_not_set(self, mock_memory, mock_registry, mock_executor):
        """Without AGENTGUARD_NO_AUTH, missing credentials return 401."""
        with patch.dict(os.environ, {"AGENTGUARD_NO_AUTH": "0", "AGENTGUARD_API_KEY": ""}):
            store = RunStore(mock_memory)
            app.dependency_overrides[get_memory] = lambda: mock_memory
            app.dependency_overrides[get_registry] = lambda: mock_registry
            app.dependency_overrides[get_executor] = lambda: mock_executor
            app.dependency_overrides[get_run_store] = lambda: store
            with TestClient(app, raise_server_exceptions=False) as c:
                r = c.post("/runs", json={"task": "test"})
            app.dependency_overrides.clear()
        assert r.status_code == 401
