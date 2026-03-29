from typing import Dict, Any, List

class Registry:
    def __init__(self):
        # In MVP, an in-memory dictionary; later Redis-backed
        self._items: Dict[str, Dict[str, Any]] = {}

    def register(self, item_id: str, role: str, semantic_description: str, input_schema: Dict[str, Any], output_schema: Dict[str, Any], endpoint: str = ""):
        self._items[item_id] = {
            "id": item_id,
            "role": role,
            "semantic_description": semantic_description,
            "input_schema": input_schema,
            "output_schema": output_schema,
            "endpoint": endpoint
        }

    def get(self, item_id: str) -> Dict[str, Any]:
        return self._items.get(item_id)

    def search_by_intent(self, intent: str) -> List[Dict[str, Any]]:
        # Mock semantic search
        results = []
        intent_lower = intent.lower()
        for item in self._items.values():
            if intent_lower in item["semantic_description"].lower() or intent_lower in item["role"].lower():
                results.append(item)
        return results

    def list_all(self) -> List[Dict[str, Any]]:
        return list(self._items.values())
