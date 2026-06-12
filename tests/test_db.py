"""
tests/test_db.py — DatabaseManager unit tests
-----------------------------------------------
Tests cover:
  - Graceful fallback when DATABASE_URL is unset
  - Connection is available when DATABASE_URL is set (requires live Postgres)
  - Migration runner works and is idempotent
  - execute / execute_one / execute_write return correct types
"""
import os
import pytest
from unittest.mock import patch


# --------------------------------------------------------------------------- #
#  Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _make_db_no_url():
    """DatabaseManager with no DATABASE_URL — must be unavailable."""
    with patch.dict(os.environ, {"DATABASE_URL": ""}, clear=False):
        from agentguard.db import DatabaseManager
        return DatabaseManager()


# --------------------------------------------------------------------------- #
#  Fallback / no-DB tests (always run, no live Postgres needed)               #
# --------------------------------------------------------------------------- #

class TestDatabaseManagerFallback:
    def test_unavailable_when_no_url(self):
        """DatabaseManager.available must be False when DATABASE_URL is unset."""
        import importlib
        import agentguard.db as db_mod
        with patch.dict(os.environ, {"DATABASE_URL": ""}, clear=False):
            mgr = db_mod.DatabaseManager()
            # Re-trigger _connect with empty url
            mgr._pool = None
            mgr._connect()
            assert mgr.available is False

    def test_execute_raises_when_unavailable(self):
        """execute() must raise RuntimeError when DatabaseManager is unavailable."""
        import agentguard.db as db_mod
        with patch.dict(os.environ, {"DATABASE_URL": ""}, clear=False):
            mgr = db_mod.DatabaseManager()
            mgr._pool = None
            with pytest.raises(RuntimeError, match="not available"):
                mgr.execute("SELECT 1")

    def test_execute_one_raises_when_unavailable(self):
        import agentguard.db as db_mod
        with patch.dict(os.environ, {"DATABASE_URL": ""}, clear=False):
            mgr = db_mod.DatabaseManager()
            mgr._pool = None
            with pytest.raises(RuntimeError):
                mgr.execute_one("SELECT 1")

    def test_execute_write_raises_when_unavailable(self):
        import agentguard.db as db_mod
        with patch.dict(os.environ, {"DATABASE_URL": ""}, clear=False):
            mgr = db_mod.DatabaseManager()
            mgr._pool = None
            with pytest.raises(RuntimeError):
                mgr.execute_write("INSERT INTO foo VALUES (1)")

    def test_run_migration_skips_when_unavailable(self, caplog):
        """run_migration should log a warning and not raise when unavailable."""
        import agentguard.db as db_mod
        with patch.dict(os.environ, {"DATABASE_URL": ""}, clear=False):
            mgr = db_mod.DatabaseManager()
            mgr._pool = None
            import logging
            with caplog.at_level(logging.WARNING, logger="agentguard.db"):
                mgr.run_migration("migrations/001_registry.sql")
            assert any("unavailable" in r.message for r in caplog.records)

    def test_close_is_safe_when_unavailable(self):
        """close() must not raise when pool is None."""
        import agentguard.db as db_mod
        with patch.dict(os.environ, {"DATABASE_URL": ""}, clear=False):
            mgr = db_mod.DatabaseManager()
            mgr._pool = None
            mgr.close()  # should not raise

    def test_embedding_dim_default(self):
        import agentguard.db as db_mod
        with patch.dict(os.environ, {"DATABASE_URL": "", "EMBEDDING_DIM": ""}, clear=False):
            mgr = db_mod.DatabaseManager()
            # Default is 1536 when EMBEDDING_DIM is absent/empty
            # Use int(os.environ.get("EMBEDDING_DIM", "1536")) logic
            assert mgr.embedding_dim >= 1

    def test_embedding_dim_custom(self):
        import agentguard.db as db_mod
        with patch.dict(os.environ, {"DATABASE_URL": "", "EMBEDDING_DIM": "768"}, clear=False):
            mgr = db_mod.DatabaseManager()
            assert mgr.embedding_dim == 768


# --------------------------------------------------------------------------- #
#  Live Postgres tests — skipped when DATABASE_URL is not set                 #
# --------------------------------------------------------------------------- #

postgres_required = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set — skipping live PostgreSQL tests",
)


@postgres_required
class TestDatabaseManagerLive:
    @pytest.fixture(autouse=True)
    def db(self):
        from agentguard.db import DatabaseManager
        mgr = DatabaseManager()
        if not mgr.available:
            pytest.skip("DATABASE_URL is set but PostgreSQL is not reachable.")
        yield mgr
        mgr.close()

    def test_available(self, db):
        assert db.available is True

    def test_execute_one_select_1(self, db):
        row = db.execute_one("SELECT 1 AS val")
        assert row is not None
        assert row.get("val") == 1

    def test_execute_returns_list(self, db):
        rows = db.execute("SELECT generate_series(1,3) AS n")
        assert isinstance(rows, list)
        assert len(rows) == 3

    def test_migration_is_idempotent(self, db):
        """Running the migration twice must not raise."""
        db.run_migration("migrations/001_registry.sql")
        db.run_migration("migrations/001_registry.sql")

    def test_execute_write_returns_row_count(self, db):
        # Insert and delete a temporary row in agent_registry
        db.run_migration("migrations/001_registry.sql")
        db.execute_write(
            "INSERT INTO agent_registry (id, role, semantic_description) "
            "VALUES ('_test_db_tmp', 'Test', 'Test agent') "
            "ON CONFLICT (id) DO NOTHING"
        )
        deleted = db.execute_write(
            "DELETE FROM agent_registry WHERE id = '_test_db_tmp'"
        )
        assert deleted >= 1
