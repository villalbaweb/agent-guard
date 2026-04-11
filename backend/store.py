"""
store.py — Run State Persistence
----------------------------------
Thin wrapper that maps run lifecycle states to MemoryManager keys.

Key schema:
  run:{id}:state   — full AgentGuardState dict (JSON)
  run:{id}:status  — lightweight status string + metadata
  run:{id}:trace   — serialized trace JSON document

All data lives in Redis (with in-memory fallback when Redis is unavailable).
"""
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from agentguard.memory import MemoryManager

logger = logging.getLogger(__name__)


class RunStore:
    """Thin facade over MemoryManager for run lifecycle management."""

    STATUS_KEY = "run:{id}:status"

    def __init__(self, memory: MemoryManager):
        self._memory = memory

    # ------------------------------------------------------------------ #
    #  Run ID generation                                                   #
    # ------------------------------------------------------------------ #

    @staticmethod
    def new_run_id() -> str:
        return f"run_{uuid.uuid4().hex[:12]}"

    # ------------------------------------------------------------------ #
    #  Status record                                                       #
    # ------------------------------------------------------------------ #

    def _status_key(self, run_id: str) -> str:
        return f"run:{run_id}:status"

    def create_run(self, run_id: str, task: str, subject: str) -> Dict[str, Any]:
        """Initialize a run record in 'queued' status."""
        record = {
            "run_id": run_id,
            "task": task,
            "subject": subject,
            "status": "queued",
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "pending_steps": [],
        }
        self._memory.set(self._status_key(run_id), record)
        return record

    def set_status(self, run_id: str, status: str, **extra):
        """Update a run's status and optionally merge extra fields."""
        record = self.get_status(run_id) or {}
        record["status"] = status
        record.update(extra)
        self._memory.set(self._status_key(run_id), record)

    def get_status(self, run_id: str) -> Optional[Dict[str, Any]]:
        return self._memory.get(self._status_key(run_id))

    # ------------------------------------------------------------------ #
    #  State and trace helpers                                             #
    # ------------------------------------------------------------------ #

    def save_state(self, run_id: str, state: Dict[str, Any]):
        self._memory.store_run(run_id, state)

    def load_state(self, run_id: str) -> Optional[Dict[str, Any]]:
        return self._memory.get_run(run_id)

    def save_trace(self, run_id: str, trace_doc: Dict[str, Any]):
        self._memory.store_trace(run_id, trace_doc)

    def load_trace(self, run_id: str) -> Optional[Dict[str, Any]]:
        return self._memory.get_trace(run_id)
