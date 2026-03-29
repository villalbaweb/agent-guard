import json
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class MemoryManager:
    def __init__(self):
        # Redis connection or mock
        self._store: Dict[str, str] = {}

    def _key_history(self, run_id: str) -> str:
        return f"run:{run_id}:history"

    def _key_blueprints(self, run_id: str) -> str:
        return f"run:{run_id}:blueprints"

    def _key_telemetry(self, run_id: str) -> str:
        return f"run:{run_id}:telemetry"

    def set(self, key: str, value: Any):
        self._store[key] = json.dumps(value)

    def get(self, key: str) -> Optional[Any]:
        val = self._store.get(key)
        if val is not None:
            return json.loads(val)
        return None

    def record_thought(self, run_id: str, thought: Dict[str, Any]):
        key = self._key_history(run_id)
        history = self.get(key) or []
        history.append(thought)
        self.set(key, history)

    def get_history(self, run_id: str) -> list:
        return self.get(self._key_history(run_id)) or []

    def detect_loop(self, run_id: str, new_thought: Dict[str, Any]) -> bool:
        # Mock loop detection (semantic similarity)
        history = self.get_history(run_id)
        if not history:
            return False

        recent = history[-3:]
        for item in recent:
             if new_thought.get("intent") == item.get("intent") and new_thought.get("action") == item.get("action"):
                 return True
        return False

    def cache_blueprint(self, run_id: str, blueprint: Dict[str, Any]):
        self.set(self._key_blueprints(run_id), blueprint)

    def get_blueprint(self, run_id: str) -> Optional[Dict[str, Any]]:
        return self.get(self._key_blueprints(run_id))
