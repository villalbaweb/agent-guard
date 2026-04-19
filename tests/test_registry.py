"""
tests/test_registry.py — Registry unit tests  (U-09 / U-12)
------------------------------------------------------------
Tests cover:
  - In-memory fallback (always run, no PostgreSQL needed)
  - Backward compatibility: Registry() with no args == current behaviour
  - PostgreSQL-backed paths (skipped when DATABASE_URL is not set)
  - Vector search vs substring fallback
  - deactivate / activate / delete
  - update_health
  - list_all(include_inactive)
  - recompute_embeddings
"""
import os
import pytest
from unittest.mock import patch, MagicMock


# --------------------------------------------------------------------------- #
#  Helpers                                                                     #
# --------------------------------------------------------------------------- #

def make_in_memory_registry():
    """Return a Registry with no PostgreSQL dependency."""
    from agentguard.registry import Registry
    return Registry(db=None)


def _seed(reg, n: int = 2):
    reg.register(
        item_id="search_agent_01",
        role="Search Specialist",
        semantic_description="Searches the web for any topic or query.",
        input_schema={"query": "string"},
        output_schema={"results": "list"},
    )
    if n >= 2:
        reg.register(
            item_id="analysis_agent_01",
            role="Data Analyst",
            semantic_description="Analyzes datasets and provides structured insights.",
            input_schema={"data": "string"},
            output_schema={"analysis": "string"},
        )


# --------------------------------------------------------------------------- #
#  Backward-compatibility / in-memory tests                                    #
# --------------------------------------------------------------------------- #

class TestRegistryInMemoryFallback:
    def test_no_args_still_works(self):
        """Registry() with no args must behave identically to the original."""
        from agentguard.registry import Registry
        reg = Registry()
        reg.register(
            item_id="a1",
            role="Role",
            semantic_description="Does things.",
            input_schema={},
            output_schema={},
        )
        assert reg.get("a1") is not None
        assert reg.get("a1")["role"] == "Role"

    def test_register_returns_dict(self):
        reg = make_in_memory_registry()
        result = reg.register(
            item_id="x1",
            role="X",
            semantic_description="Executes X tasks.",
            input_schema={},
            output_schema={},
        )
        assert isinstance(result, dict)
        assert result["id"] == "x1"

    def test_list_all_returns_active_agents(self):
        reg = make_in_memory_registry()
        _seed(reg)
        agents = reg.list_all()
        assert len(agents) == 2

    def test_list_all_include_inactive(self):
        reg = make_in_memory_registry()
        _seed(reg)
        reg.deactivate("search_agent_01")
        active = reg.list_all(include_inactive=False)
        all_agents = reg.list_all(include_inactive=True)
        assert len(active) == 1
        assert len(all_agents) == 2

    def test_search_by_intent_substring(self):
        reg = make_in_memory_registry()
        _seed(reg)
        results = reg.search_by_intent("search")
        assert any(r["id"] == "search_agent_01" for r in results)

    def test_search_by_intent_excludes_inactive(self):
        reg = make_in_memory_registry()
        _seed(reg)
        reg.deactivate("search_agent_01")
        results = reg.search_by_intent("search")
        ids = [r["id"] for r in results]
        assert "search_agent_01" not in ids

    def test_get_returns_none_for_missing(self):
        reg = make_in_memory_registry()
        assert reg.get("does_not_exist") is None

    def test_get_returns_none_for_inactive(self):
        reg = make_in_memory_registry()
        _seed(reg, n=1)
        reg.deactivate("search_agent_01")
        assert reg.get("search_agent_01") is None

    def test_deactivate_returns_true_when_found(self):
        reg = make_in_memory_registry()
        _seed(reg, n=1)
        assert reg.deactivate("search_agent_01") is True

    def test_deactivate_returns_false_when_not_found(self):
        reg = make_in_memory_registry()
        assert reg.deactivate("ghost") is False

    def test_activate_reactivates_agent(self):
        reg = make_in_memory_registry()
        _seed(reg, n=1)
        reg.deactivate("search_agent_01")
        reg.activate("search_agent_01")
        assert reg.get("search_agent_01") is not None

    def test_delete_removes_agent(self):
        reg = make_in_memory_registry()
        _seed(reg, n=1)
        assert reg.delete("search_agent_01") is True
        assert reg.get("search_agent_01") is None

    def test_delete_returns_false_when_not_found(self):
        reg = make_in_memory_registry()
        assert reg.delete("ghost") is False

    def test_update_health_stored(self):
        reg = make_in_memory_registry()
        _seed(reg, n=1)
        reg.update_health("search_agent_01", "healthy")
        agent = reg.get("search_agent_01")
        assert agent["health_status"] == "healthy"

    def test_recompute_embeddings_returns_0_without_db(self, caplog):
        import logging
        reg = make_in_memory_registry()
        with caplog.at_level(logging.WARNING, logger="agentguard.registry"):
            count = reg.recompute_embeddings()
        assert count == 0

    def test_register_upsert_updates_role(self):
        """Re-registering the same id updates the record."""
        reg = make_in_memory_registry()
        reg.register(
            item_id="a1", role="Old Role",
            semantic_description="Old desc.",
            input_schema={}, output_schema={},
        )
        reg.register(
            item_id="a1", role="New Role",
            semantic_description="New desc.",
            input_schema={}, output_schema={},
        )
        assert reg.get("a1")["role"] == "New Role"

    def test_search_limit(self):
        reg = make_in_memory_registry()
        for i in range(10):
            reg.register(
                item_id=f"agent_{i:02d}",
                role=f"Search Agent {i}",
                semantic_description=f"Searches for topic {i}.",
                input_schema={}, output_schema={},
            )
        results = reg.search_by_intent("search", limit=3)
        assert len(results) <= 3


# --------------------------------------------------------------------------- #
#  Live Postgres tests                                                          #
# --------------------------------------------------------------------------- #

postgres_required = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set — skipping live PostgreSQL tests",
)


@postgres_required
class TestRegistryPostgres:
    @pytest.fixture(autouse=True)
    def registry(self):
        from agentguard.db import DatabaseManager
        from agentguard.registry import Registry
        db = DatabaseManager()
        reg = Registry(db=db)
        # Clean up test data before and after each test
        try:
            db.execute_write("DELETE FROM agent_registry WHERE id LIKE '_pg_test_%'")
        except Exception:
            pass
        yield reg
        try:
            db.execute_write("DELETE FROM agent_registry WHERE id LIKE '_pg_test_%'")
        except Exception:
            pass
        db.close()

    def test_register_persists(self, registry):
        registry.register(
            item_id="_pg_test_01",
            role="PG Test",
            semantic_description="Persists in PostgreSQL.",
            input_schema={},
            output_schema={},
        )
        agent = registry.get("_pg_test_01")
        assert agent is not None
        assert agent["role"] == "PG Test"

    def test_upsert_updates_role(self, registry):
        registry.register(
            item_id="_pg_test_02",
            role="Old Role",
            semantic_description="Old description.",
            input_schema={},
            output_schema={},
        )
        registry.register(
            item_id="_pg_test_02",
            role="New Role",
            semantic_description="Old description.",
            input_schema={},
            output_schema={},
        )
        agent = registry.get("_pg_test_02")
        assert agent["role"] == "New Role"

    def test_deactivate_excludes_from_search(self, registry):
        registry.register(
            item_id="_pg_test_03",
            role="Findable",
            semantic_description="Findable by intent.",
            input_schema={},
            output_schema={},
        )
        registry.deactivate("_pg_test_03")
        results = registry.search_by_intent("Findable")
        ids = [r["id"] for r in results]
        assert "_pg_test_03" not in ids

    def test_activate_reactivates(self, registry):
        registry.register(
            item_id="_pg_test_04",
            role="Dormant",
            semantic_description="Temporarily deactivated.",
            input_schema={},
            output_schema={},
        )
        registry.deactivate("_pg_test_04")
        assert registry.get("_pg_test_04") is None
        registry.activate("_pg_test_04")
        assert registry.get("_pg_test_04") is not None

    def test_hard_delete(self, registry):
        registry.register(
            item_id="_pg_test_05",
            role="Gone",
            semantic_description="Will be deleted.",
            input_schema={},
            output_schema={},
        )
        registry.delete("_pg_test_05")
        assert registry.get("_pg_test_05") is None

    def test_update_health(self, registry):
        registry.register(
            item_id="_pg_test_06",
            role="Health",
            semantic_description="Has health status.",
            input_schema={},
            output_schema={},
        )
        registry.update_health("_pg_test_06", "healthy")
        agent = registry.get("_pg_test_06")
        assert agent["health_status"] == "healthy"
        assert agent["last_heartbeat"] is not None
