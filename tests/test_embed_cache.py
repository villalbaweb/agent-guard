import os
import json
import pytest
from unittest.mock import patch, MagicMock
from agentguard.llm import get_embeddings

@pytest.fixture
def mock_redis():
    class MockRedis:
        def __init__(self):
            self.store = {}
        def get(self, key):
            return self.store.get(key)
        def setex(self, key, ttl, value):
            self.store[key] = value
    return MockRedis()

def test_cached_embeddings(mock_redis):
    # Setup environ for OpenAI to simulate base layer
    with patch.dict(os.environ, {"OPENAI_API_KEY": "fake-key"}):
        with patch("agentguard.llm._get_redis", return_value=mock_redis), \
             patch("langchain_openai.OpenAIEmbeddings") as mock_openai:
             
             # Mock the underlying provider
             mock_instance = MagicMock()
             mock_instance.embed_documents.side_effect = lambda texts: [[float(len(t))] for t in texts]
             mock_openai.return_value = mock_instance
             
             embed_fn = get_embeddings()
             assert embed_fn is not None
             
             # First call - cache miss
             texts1 = ["hello", "world"]
             res1 = embed_fn(texts1)
             assert res1 == [[5.0], [5.0]]
             assert mock_instance.embed_documents.call_count == 1
             
             # Second call - same exact texts - cache hit
             res2 = embed_fn(["hello", "world"])
             assert res2 == [[5.0], [5.0]]
             assert mock_instance.embed_documents.call_count == 1  # Should NOT increase
             
             # Third call - partially cached
             res3 = embed_fn(["hello", "new_world"])
             assert res3 == [[5.0], [9.0]]
             assert mock_instance.embed_documents.call_count == 2
             # embed_documents should only receive ["new_world"]
             mock_instance.embed_documents.assert_called_with(["new_world"])
