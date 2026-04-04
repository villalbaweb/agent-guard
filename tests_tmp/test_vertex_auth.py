import os
import unittest
from unittest.mock import patch

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from agentguard.llm import get_llm

class TestVertexAuth(unittest.TestCase):
    
    @patch.dict(os.environ, {
        "VERTEX_API_KEY": "test-api-key",
        "VERTEX_PROJECT_ID": "test-project-id"
    })
    def test_get_llm_vertex_api_key(self):
        """Test that get_llm returns ChatVertexAI when VERTEX_API_KEY and VERTEX_PROJECT_ID are set."""
        # We need to mock the import or ensure the environment is clean of other higher-priority keys
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "", "GOOGLE_APPLICATION_CREDENTIALS": ""}):
            llm = get_llm()
            
            self.assertIsNotNone(llm, "LLM should not be None")
            # Check if it's the correct class
            self.assertEqual(llm.__class__.__name__, "ChatVertexAI")
            
            print(f"Successfully initialized: {llm.__class__.__name__}")

if __name__ == "__main__":
    print("Testing Vertex AI API Key configuration...")
    # Clean env for manual run if needed:
    # os.environ["VERTEX_API_KEY"] = "your-key"
    # os.environ["VERTEX_PROJECT_ID"] = "your-project"
    
    unittest.main()
