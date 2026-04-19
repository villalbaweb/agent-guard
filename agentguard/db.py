"""
db.py — PostgreSQL Connection Manager
---------------------------------------
Provides a singleton connection pool backed by psycopg3 + psycopg-pool.
Registers pgvector type adapters so that Python lists/numpy arrays can be
stored in and retrieved from `vector` columns transparently.

Graceful degradation
--------------------
If DATABASE_URL is not set, or PostgreSQL is unreachable at startup, the pool
is left at None and ``DatabaseManager.available`` returns False.  Callers
(Registry, health endpoint) check this flag and fall back to their in-memory
implementations.  No PostgreSQL required for local development.

Environment variables
---------------------
DATABASE_URL      PostgreSQL DSN.
                  Default: postgresql://agent_user:changeme@localhost:5432/agent_db
DB_POOL_MIN       Minimum pool connections. Default: 2
DB_POOL_MAX       Maximum pool connections. Default: 10
EMBEDDING_DIM     Expected embedding dimension. Default: 1536
"""
from __future__ import annotations

import logging
import os
import pathlib
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DEFAULT_DATABASE_URL = (
    "postgresql://agent_user:changeme@localhost:5432/agent_db"
)


class DatabaseManager:
    """PostgreSQL connection pool with pgvector support.

    Usage::

        db = DatabaseManager()
        if db.available:
            rows = db.execute("SELECT * FROM agent_registry WHERE is_active = TRUE")
    """

    def __init__(self) -> None:
        self._pool: Any = None  # psycopg_pool.ConnectionPool | None
        self._embedding_dim: int = int(os.environ.get("EMBEDDING_DIM", "") or "1536")
        self._connect()

    # ---------------------------------------------------------------------- #
    #  Internal helpers                                                        #
    # ---------------------------------------------------------------------- #

    def _connect(self) -> None:
        """Open the connection pool.  Sets self._pool = None on failure."""
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            logger.info(
                "DATABASE_URL not set — DatabaseManager running in unavailable mode. "
                "Registry will use in-memory fallback."
            )
            return

        try:
            import psycopg_pool  # type: ignore[import-untyped]
            import psycopg  # type: ignore[import-untyped]
            from pgvector.psycopg import register_vector  # type: ignore[import-untyped]

            # Open the pool with a short connection timeout so startup is fast.
            pool = psycopg_pool.ConnectionPool(
                conninfo=url,
                min_size=int(os.environ.get("DB_POOL_MIN", "2")),
                max_size=int(os.environ.get("DB_POOL_MAX", "10")),
                open=False,
            )
            pool.open(wait=True, timeout=10)

            # Register pgvector adapter for all connections in the pool
            with pool.connection() as conn:
                register_vector(conn)

            self._pool = pool
            logger.info(
                "DatabaseManager: connected to PostgreSQL "
                f"(pool min={pool.min_size} max={pool.max_size}, "
                f"embedding_dim={self._embedding_dim})"
            )
        except ImportError as exc:
            logger.warning(
                f"psycopg / psycopg-pool / pgvector not installed ({exc}). "
                "DatabaseManager unavailable — install with: "
                "pip install 'psycopg[binary]' psycopg-pool pgvector"
            )
        except Exception as exc:
            logger.warning(
                f"DatabaseManager: could not connect to PostgreSQL ({exc}). "
                "Registry will use in-memory fallback."
            )

    # ---------------------------------------------------------------------- #
    #  Public API                                                              #
    # ---------------------------------------------------------------------- #

    @property
    def available(self) -> bool:
        """True if the connection pool is open and healthy."""
        return self._pool is not None

    @property
    def embedding_dim(self) -> int:
        """Expected embedding vector dimension (from EMBEDDING_DIM env var)."""
        return self._embedding_dim

    def execute(self, query: str, params: tuple = ()) -> list[dict]:
        """Execute a SELECT query and return all rows as dicts."""
        if not self.available:
            raise RuntimeError("DatabaseManager is not available.")
        with self._pool.connection() as conn:
            with conn.cursor(row_factory=self._row_factory()) as cur:
                cur.execute(query, params)
                return cur.fetchall()  # type: ignore[return-value]

    def execute_one(self, query: str, params: tuple = ()) -> Optional[dict]:
        """Execute a SELECT query and return the first row, or None."""
        if not self.available:
            raise RuntimeError("DatabaseManager is not available.")
        with self._pool.connection() as conn:
            with conn.cursor(row_factory=self._row_factory()) as cur:
                cur.execute(query, params)
                return cur.fetchone()  # type: ignore[return-value]

    def execute_write(self, query: str, params: tuple = ()) -> int:
        """Execute an INSERT/UPDATE/DELETE and return affected row count."""
        if not self.available:
            raise RuntimeError("DatabaseManager is not available.")
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                return cur.rowcount

    def run_migration(self, migration_file: str) -> None:
        """Execute a SQL migration file idempotently.

        Args:
            migration_file: Absolute or relative path to a .sql file.
        """
        if not self.available:
            logger.warning("DatabaseManager unavailable — skipping migration.")
            return
        path = pathlib.Path(migration_file)
        if not path.exists():
            raise FileNotFoundError(f"Migration file not found: {migration_file}")
        sql = path.read_text(encoding="utf-8")
        with self._pool.connection() as conn:
            conn.execute(sql)
        logger.info(f"DatabaseManager: migration applied — {path.name}")

    def close(self) -> None:
        """Close the connection pool."""
        if self._pool is not None:
            try:
                self._pool.close()
            except Exception:
                pass
            self._pool = None
            logger.info("DatabaseManager: pool closed.")

    # ---------------------------------------------------------------------- #
    #  Internal                                                                #
    # ---------------------------------------------------------------------- #

    @staticmethod
    def _row_factory():
        """Return a psycopg row_factory that produces plain dicts."""
        try:
            from psycopg.rows import dict_row  # type: ignore[import-untyped]
            return dict_row
        except ImportError:
            return None
