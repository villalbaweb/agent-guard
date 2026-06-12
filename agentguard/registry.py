"""
registry.py — Agent Capability Registry
-----------------------------------------
Maintains a catalogue of registered agents (id, role, semantic_description,
input/output schemas, endpoint).

Storage backends (selected automatically at construction time):
  1. PostgreSQL + pgvector  — for vector cosine similarity search (best)
  2. PostgreSQL only        — for ILIKE substring search with persistence
  3. In-memory dict         — original behaviour; no persistence (fallback)

The public API is identical in all three modes so callers are unaware of
which backend is active.  Pass ``db=None`` (the default) to get the original
in-memory behaviour regardless of DATABASE_URL.

Example::

    # Fully automatic (uses PostgreSQL when available)
    from agentguard.db import DatabaseManager
    db = DatabaseManager()
    registry = Registry(db=db)
    registry.register(item_id="agent_01", role="...", ...)
    top_matches = registry.search_by_intent("analyse market trends")

    # Backward-compatible (always in-memory)
    registry = Registry()
    registry.register(item_id="agent_01", role="...", ...)
"""
from __future__ import annotations

import logging
import os
import pathlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Path to the SQL migration file relative to this module's package root.
_MIGRATION_FILE = pathlib.Path(__file__).parent.parent / "migrations" / "001_registry.sql"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _row_to_dict(row: dict) -> dict:
    """Normalise a PostgreSQL row dict for external consumption.

    Converts pgvector numpy arrays to a simple bool ``has_embedding`` and
    removes the raw embedding bytes (callers never need the raw vector).
    """
    out = dict(row)
    embedding = out.pop("embedding", None)
    out["has_embedding"] = embedding is not None
    return out


class Registry:
    """Agent capability registry with optional PostgreSQL + pgvector backend."""

    def __init__(self, db=None) -> None:
        """
        Args:
            db: A ``DatabaseManager`` instance.  When provided *and* available,
                PostgreSQL is used for storage and search.  Pass ``None`` (the
                default) to use the in-memory fallback.
        """
        self._db = db if (db is not None and db.available) else None
        self._items: Dict[str, Dict[str, Any]] = {}  # in-memory store

        if self._db is not None:
            # Run the idempotent migration to ensure the table exists.
            try:
                self._db.run_migration(str(_MIGRATION_FILE))
            except Exception as exc:
                logger.warning(f"Registry: migration failed ({exc}) — degrading to in-memory.")
                self._db = None

        mode = "PostgreSQL" if self._db is not None else "in-memory"
        logger.info(f"Registry: using {mode} backend.")

    # ---------------------------------------------------------------------- #
    #  Write Operations                                                        #
    # ---------------------------------------------------------------------- #

    def register(
        self,
        item_id: str,
        role: str,
        semantic_description: str,
        input_schema: Dict[str, Any],
        output_schema: Dict[str, Any],
        endpoint: str = "",
    ) -> dict:
        """Register or update an agent.

        When PostgreSQL is available:
          1. Compute embedding of *semantic_description* via ``get_embeddings()``.
          2. UPSERT into ``agent_registry``.
          3. Return the stored record as a dict.

        When PostgreSQL is unavailable:
          Store in the in-memory dict and return an equivalent dict.

        Returns:
            The agent metadata dict (includes ``registered_at``, ``updated_at``).
        """
        if self._db is not None:
            return self._register_pg(
                item_id, role, semantic_description,
                input_schema, output_schema, endpoint,
            )
        return self._register_mem(
            item_id, role, semantic_description,
            input_schema, output_schema, endpoint,
        )

    def _register_pg(
        self,
        item_id: str,
        role: str,
        semantic_description: str,
        input_schema: Dict[str, Any],
        output_schema: Dict[str, Any],
        endpoint: str,
    ) -> dict:
        import json as _json

        embedding = self._compute_embedding(semantic_description)

        # Validate dimension if we got an embedding
        if embedding is not None and self._db is not None:
            if len(embedding) != self._db.embedding_dim:
                logger.warning(
                    f"Registry: embedding dimension mismatch for '{item_id}': "
                    f"expected {self._db.embedding_dim}, got {len(embedding)}. "
                    "Storing without embedding."
                )
                embedding = None

        sql = """
            INSERT INTO agent_registry
                (id, role, semantic_description, input_schema, output_schema, endpoint, embedding)
            VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                role                 = EXCLUDED.role,
                semantic_description = EXCLUDED.semantic_description,
                input_schema         = EXCLUDED.input_schema,
                output_schema        = EXCLUDED.output_schema,
                endpoint             = EXCLUDED.endpoint,
                embedding            = EXCLUDED.embedding,
                is_active            = TRUE,
                updated_at           = NOW()
        """
        try:
            self._db.execute_write(
                sql,
                (
                    item_id,
                    role,
                    semantic_description,
                    _json.dumps(input_schema),
                    _json.dumps(output_schema),
                    endpoint,
                    embedding,
                ),
            )
            row = self._db.execute_one(
                "SELECT * FROM agent_registry WHERE id = %s", (item_id,)
            )
            return _row_to_dict(row) if row else {}
        except Exception as exc:
            logger.error(f"Registry: PostgreSQL register failed ({exc}) — falling back to memory.")
            self._db = None
            return self._register_mem(
                item_id, role, semantic_description, input_schema, output_schema, endpoint
            )

    def _register_mem(
        self,
        item_id: str,
        role: str,
        semantic_description: str,
        input_schema: Dict[str, Any],
        output_schema: Dict[str, Any],
        endpoint: str,
    ) -> dict:
        now = _now_utc()
        record: Dict[str, Any] = {
            "id": item_id,
            "role": role,
            "semantic_description": semantic_description,
            "input_schema": input_schema,
            "output_schema": output_schema,
            "endpoint": endpoint,
            "is_active": True,
            "has_embedding": False,
            "health_status": "unknown",
            "last_heartbeat": None,
            "registered_at": self._items.get(item_id, {}).get("registered_at", now),
            "updated_at": now,
        }
        self._items[item_id] = record
        return dict(record)

    def deactivate(self, item_id: str) -> bool:
        """Soft-delete: set is_active=FALSE.  Agent excluded from searches."""
        if self._db is not None:
            rows = self._db.execute_write(
                "UPDATE agent_registry SET is_active = FALSE WHERE id = %s", (item_id,)
            )
            return rows > 0
        if item_id in self._items:
            self._items[item_id]["is_active"] = False
            return True
        return False

    def activate(self, item_id: str) -> bool:
        """Re-activate a previously deactivated agent."""
        if self._db is not None:
            rows = self._db.execute_write(
                "UPDATE agent_registry SET is_active = TRUE WHERE id = %s", (item_id,)
            )
            return rows > 0
        if item_id in self._items:
            self._items[item_id]["is_active"] = True
            return True
        return False

    def delete(self, item_id: str) -> bool:
        """Hard-delete an agent from the registry."""
        if self._db is not None:
            rows = self._db.execute_write(
                "DELETE FROM agent_registry WHERE id = %s", (item_id,)
            )
            return rows > 0
        if item_id in self._items:
            del self._items[item_id]
            return True
        return False

    def update_health(self, item_id: str, status: str) -> None:
        """Update health_status and last_heartbeat for an agent."""
        if self._db is not None:
            self._db.execute_write(
                """
                UPDATE agent_registry
                SET health_status = %s, last_heartbeat = NOW()
                WHERE id = %s
                """,
                (status, item_id),
            )
        elif item_id in self._items:
            self._items[item_id]["health_status"] = status
            self._items[item_id]["last_heartbeat"] = _now_utc()

    # ---------------------------------------------------------------------- #
    #  Read Operations                                                         #
    # ---------------------------------------------------------------------- #

    def get(self, item_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve an active agent by ID. Returns None if not found or inactive."""
        if self._db is not None:
            row = self._db.execute_one(
                "SELECT * FROM agent_registry WHERE id = %s AND is_active = TRUE",
                (item_id,),
            )
            return _row_to_dict(row) if row else None
        item = self._items.get(item_id)
        if item and item.get("is_active", True):
            return dict(item)
        return None

    def list_all(self, include_inactive: bool = False) -> List[Dict[str, Any]]:
        """List all registered agents.

        Args:
            include_inactive: If True, include soft-deleted agents.
        """
        if self._db is not None:
            if include_inactive:
                rows = self._db.execute("SELECT * FROM agent_registry ORDER BY registered_at")
            else:
                rows = self._db.execute(
                    "SELECT * FROM agent_registry WHERE is_active = TRUE ORDER BY registered_at"
                )
            return [_row_to_dict(r) for r in rows]
        # In-memory
        items = list(self._items.values())
        if not include_inactive:
            items = [i for i in items if i.get("is_active", True)]
        return [dict(i) for i in items]

    def search_by_intent(self, intent: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Find agents whose semantic_description best matches the intent.

        Search modes (selected automatically):
        - **Vector cosine similarity** — when PostgreSQL + embeddings are both available.
        - **PostgreSQL ILIKE** — when only PostgreSQL is available (no embedding provider).
        - **In-memory substring** — original behaviour (no PostgreSQL).

        Args:
            intent: Natural-language description of the desired capability.
            limit: Maximum number of results to return.

        Returns:
            List of agent dicts ordered by relevance descending.
        """
        if self._db is not None:
            return self._search_pg(intent, limit)
        return self._search_mem(intent, limit)

    def _search_pg(self, intent: str, limit: int) -> List[Dict[str, Any]]:
        # Try vector search first
        embedding = self._compute_embedding(intent)
        if embedding is not None:
            try:
                rows = self._db.execute(
                    """
                    SELECT * FROM agent_registry
                    WHERE is_active = TRUE AND embedding IS NOT NULL
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (embedding, limit),
                )
                if rows:
                    return [_row_to_dict(r) for r in rows]
                # No embedded agents yet — fall through to ILIKE
            except Exception as exc:
                logger.warning(f"Registry: vector search failed ({exc}) — falling back to ILIKE.")

        # ILIKE fallback
        try:
            pattern = f"%{intent}%"
            rows = self._db.execute(
                """
                SELECT * FROM agent_registry
                WHERE is_active = TRUE
                  AND (semantic_description ILIKE %s OR role ILIKE %s)
                LIMIT %s
                """,
                (pattern, pattern, limit),
            )
            return [_row_to_dict(r) for r in rows]
        except Exception as exc:
            logger.error(f"Registry: ILIKE search failed ({exc}).")
            return []

    def _search_mem(self, intent: str, limit: int) -> List[Dict[str, Any]]:
        intent_lower = intent.lower()
        results = []
        for item in self._items.values():
            if not item.get("is_active", True):
                continue
            if (
                intent_lower in item["semantic_description"].lower()
                or intent_lower in item["role"].lower()
            ):
                results.append(dict(item))
        return results[:limit]

    # ---------------------------------------------------------------------- #
    #  Utility                                                                 #
    # ---------------------------------------------------------------------- #

    def recompute_embeddings(self) -> int:
        """Re-embed all agents' semantic_description fields.

        Useful after switching embedding providers or models.

        Returns:
            The number of agents updated.
        """
        if self._db is None:
            logger.warning("Registry: recompute_embeddings requires PostgreSQL — skipping.")
            return 0

        rows = self._db.execute(
            "SELECT id, semantic_description FROM agent_registry WHERE is_active = TRUE"
        )
        updated = 0
        for row in rows:
            emb = self._compute_embedding(row["semantic_description"])
            if emb is None:
                continue
            self._db.execute_write(
                "UPDATE agent_registry SET embedding = %s::vector WHERE id = %s",
                (emb, row["id"]),
            )
            updated += 1
        logger.info(f"Registry: recomputed embeddings for {updated} agents.")
        return updated

    async def check_agent_health(self, item_id: str) -> str:
        """Probe an agent's endpoint (if set) and update its health_status.

        Returns:
            'healthy', 'unhealthy', or 'unknown' (no endpoint configured).
        """
        agent = self.get(item_id)
        if not agent:
            return "unknown"
        endpoint = agent.get("endpoint", "")
        if not endpoint:
            return "unknown"

        # Probe the endpoint (or endpoint + /health)
        probe_url = endpoint if endpoint.endswith("/health") else f"{endpoint}/health"
        timeout = int(os.environ.get("AGENT_HEALTH_TIMEOUT", "5"))
        try:
            import httpx

            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(probe_url)
            status = "healthy" if resp.status_code < 400 else "unhealthy"
        except Exception:
            status = "unhealthy"

        self.update_health(item_id, status)
        return status

    # ---------------------------------------------------------------------- #
    #  Internal                                                                #
    # ---------------------------------------------------------------------- #

    @staticmethod
    def _compute_embedding(text: str) -> Optional[list]:
        """Embed *text* using the configured embeddings provider.

        Returns None if no provider is configured.
        """
        from agentguard.llm import get_embeddings

        embed_fn = get_embeddings()
        if embed_fn is None:
            return None
        try:
            result = embed_fn([text])
            if result and len(result) > 0:
                return result[0]
        except Exception as exc:
            logger.warning(f"Registry: embedding failed ({exc}).")
        return None
