"""
MemoryManager
-------------
Shared epistemic memory for AgentGuard runs.

Backends (resolved at init time):
  - Redis  : used when REDIS_URL is set and the server is reachable.
  - In-memory dict : silent fallback for local dev without Docker.

Loop detection strategies:
  - semantic  : cosine similarity on embeddings from the local inference service.
                Requires the embeddings container from docker-compose.poc.yml.
  - exact     : string equality on action+intent (fallback when embeddings unavailable).

Concurrency Note:
  With parallel execution (via LangGraph Send), multiple workers may call `record_thought`
  and `detect_loop` concurrently for the same `run_id`. Because loop detection checks
  similarity against any recent thought (not a specific sequence), interleaved ordering
  does not affect correctness. Redis `SET`/`GET` operations are atomic.
"""
import json
import math
import logging
import os
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


def _cosine_similarity(a: list, b: list) -> float:
    """Pure-Python cosine similarity — avoids a numpy dependency on the host."""
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


class MemoryManager:
    def __init__(self):
        self._redis = None
        self._store: Dict[str, str] = {}  # in-memory fallback

        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        try:
            import redis as redis_lib
            client = redis_lib.from_url(redis_url, decode_responses=True)
            client.ping()
            self._redis = client
            logger.info(f"MemoryManager: connected to Redis at {redis_url}")
        except Exception as e:
            logger.warning(
                f"MemoryManager: Redis not reachable ({e}). "
                "Using in-memory fallback — state will not persist across restarts."
            )

        # Lazy-load the embeddings callable once
        self._embed = None
        self._embed_checked = False

    def _get_embed(self):
        """Returns the embeddings callable, probing once and caching the result."""
        if not self._embed_checked:
            from .llm import get_embeddings
            self._embed = get_embeddings()
            self._embed_checked = True
        return self._embed

    # ------------------------------------------------------------------ #
    #  Low-level get / set                                                 #
    # ------------------------------------------------------------------ #

    def _set(self, key: str, value: Any):
        serialized = json.dumps(value)
        if self._redis:
            self._redis.set(key, serialized)
        else:
            self._store[key] = serialized

    def _get(self, key: str) -> Optional[Any]:
        raw = self._redis.get(key) if self._redis else self._store.get(key)
        if raw is not None:
            return json.loads(raw)
        return None

    # ------------------------------------------------------------------ #
    #  Key helpers                                                         #
    # ------------------------------------------------------------------ #

    def _key_history(self, run_id: str) -> str:
        return f"run:{run_id}:history"

    def _key_blueprints(self, run_id: str) -> str:
        return f"run:{run_id}:blueprints"

    def _key_telemetry(self, run_id: str) -> str:
        return f"run:{run_id}:telemetry"

    # ------------------------------------------------------------------ #
    #  Thought history (loop detection feed)                               #
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    #  D-01 — Distributed lock (Redlock-style via Redis SETNX)            #
    # ------------------------------------------------------------------ #

    def _acquire_lock(self, lock_key: str, ttl_seconds: int = 5) -> bool:
        """Try to acquire a Redis SETNX lock.  Returns True on success.

        Falls back to True for in-memory mode (single-process, no contention).
        Demonstrates the Redlock pattern: use before any read-modify-write
        cycle on shared state to prevent lost-update races under parallel Send.
        """
        if not self._redis:
            return True
        import time
        deadline = time.monotonic() + ttl_seconds
        while time.monotonic() < deadline:
            if self._redis.set(lock_key, "1", nx=True, ex=ttl_seconds):
                return True
            time.sleep(0.01)
        logger.warning(f"MemoryManager: could not acquire lock '{lock_key}' within {ttl_seconds}s.")
        return False

    def _release_lock(self, lock_key: str):
        if self._redis:
            self._redis.delete(lock_key)

    def record_thought(self, run_id: str, thought: Dict[str, Any]):
        key = self._key_history(run_id)
        lock_key = f"lock:{key}"
        self._acquire_lock(lock_key)
        try:
            history = self._get(key) or []
            history.append(thought)
            self._set(key, history)
        finally:
            self._release_lock(lock_key)

    def get_history(self, run_id: str) -> list:
        return self._get(self._key_history(run_id)) or []

    def clear_history(self, run_id: str) -> int:
        """Drop the thought history for a run, returning how many were dropped.

        Used when a human interjects mid-run: the prior thoughts describe a
        path the operator has just overridden, so keeping them would make loop
        detection fire against reasoning that is no longer relevant.
        """
        key = self._key_history(run_id)
        lock_key = f"lock:{key}"
        self._acquire_lock(lock_key)
        try:
            dropped = len(self._get(key) or [])
            self._set(key, [])
            return dropped
        finally:
            self._release_lock(lock_key)

    def detect_loop(
        self,
        run_id: str,
        new_thought: Dict[str, Any],
        similarity_threshold: float = 0.92,
        lookback_window: int = 5,
    ) -> bool:
        """
        Returns True if new_thought is semantically equivalent to a recent thought.

        Uses cosine similarity when the embeddings service is available;
        falls back to exact string match otherwise.
        """
        history = self.get_history(run_id)
        if not history:
            return False

        recent = history[-lookback_window:]
        new_text = f"{new_thought.get('action', '')} {new_thought.get('intent', '')}"

        embed = self._get_embed()
        if embed:
            # Semantic path
            past_texts = [
                f"{t.get('action', '')} {t.get('intent', '')}" for t in recent
            ]
            all_texts = past_texts + [new_text]
            try:
                vectors = embed(all_texts)
                if vectors:
                    new_vec = vectors[-1]
                    for past_vec in vectors[:-1]:
                        if _cosine_similarity(past_vec, new_vec) >= similarity_threshold:
                            logger.warning(
                                f"Semantic loop detected for run {run_id}: "
                                f"similarity >= {similarity_threshold}"
                            )
                            return True
                    return False
            except Exception as e:
                logger.error(f"Semantic loop detection failed: {e}. Falling back to exact-match.")

        # Exact-match fallback
        for item in recent:
            if (
                new_thought.get("intent") == item.get("intent")
                and new_thought.get("action") == item.get("action")
            ):
                logger.warning(f"Exact-match loop detected for run {run_id}.")
                return True

        return False

    # ------------------------------------------------------------------ #
    #  Blueprint cache                                                     #
    # ------------------------------------------------------------------ #

    def cache_blueprint(self, run_id: str, blueprint: Dict[str, Any]):
        self._set(self._key_blueprints(run_id), blueprint)

    def get_blueprint(self, run_id: str) -> Optional[Dict[str, Any]]:
        return self._get(self._key_blueprints(run_id))

    # ------------------------------------------------------------------ #
    #  Run state helpers (Step B — REST API)                              #
    # ------------------------------------------------------------------ #

    def _key_run(self, run_id: str) -> str:
        return f"run:{run_id}:state"

    def _key_trace(self, run_id: str) -> str:
        return f"run:{run_id}:trace"

    def store_run(self, run_id: str, state: Any):
        """Persist a complete AgentGuardState dict under run:{run_id}:state."""
        self._set(self._key_run(run_id), state)

    def get_run(self, run_id: str) -> Optional[Any]:
        """Retrieve a stored AgentGuardState dict, or None if not found."""
        return self._get(self._key_run(run_id))

    def store_trace(self, run_id: str, trace_doc: Any):
        """Persist a serialized trace JSON document under run:{run_id}:trace."""
        self._set(self._key_trace(run_id), trace_doc)

    def get_trace(self, run_id: str) -> Optional[Any]:
        """Retrieve a stored trace document, or None if not found."""
        return self._get(self._key_trace(run_id))

    # ------------------------------------------------------------------ #
    #  Consumption accounting (external agents via /api/gatekeeper)       #
    # ------------------------------------------------------------------ #

    def _key_consumption(self, run_id: str) -> str:
        return f"run:{run_id}:consumption"

    def get_consumption(self, run_id: str) -> Dict[str, float]:
        """Cumulative cost/steps/depth reported so far for a run."""
        return self._get(self._key_consumption(run_id)) or {
            "total_cost": 0.0,
            "steps": 0,
            "max_depth_seen": 0,
        }

    def record_consumption(
        self, run_id: str, cost: float, steps: int = 1, depth: int = 0
    ) -> Dict[str, float]:
        """Accumulate usage for a run and return the new totals.

        Locked read-modify-write: parallel workers in a recursive graph report
        concurrently, and a lost update here would under-count the budget the
        circuit breaker is enforcing.
        """
        key = self._key_consumption(run_id)
        lock_key = f"lock:{key}"
        self._acquire_lock(lock_key)
        try:
            usage = self._get(key) or {"total_cost": 0.0, "steps": 0, "max_depth_seen": 0}
            usage["total_cost"] = round(usage.get("total_cost", 0.0) + (cost or 0.0), 8)
            usage["steps"] = usage.get("steps", 0) + (steps or 0)
            usage["max_depth_seen"] = max(usage.get("max_depth_seen", 0), depth or 0)
            self._set(key, usage)
            return usage
        finally:
            self._release_lock(lock_key)

    # ------------------------------------------------------------------ #
    #  Generic key/value (for telemetry and ad-hoc state)                 #
    # ------------------------------------------------------------------ #

    def set(self, key: str, value: Any):
        self._set(key, value)

    def get(self, key: str) -> Optional[Any]:
        return self._get(key)
