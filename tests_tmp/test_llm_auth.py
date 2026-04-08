import os
import unittest
from unittest.mock import patch

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from agentguard.llm import get_llm

class TestLLMAuth(unittest.TestCase):
    
    @patch.dict(os.environ, {
        "GOOGLE_API_KEY": "test-google-api-key"
    })
    def test_get_llm_google_api_key(self):
        """Test that get_llm returns ChatGoogleGenerativeAI when GOOGLE_API_KEY is set."""
        # Ensure no other keys are present
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "", "GOOGLE_APPLICATION_CREDENTIALS": "", "OPENAI_API_KEY": ""}):
            llm = get_llm()
            
            self.assertIsNotNone(llm, "LLM should not be None")
            # Check if it's the correct class
            self.assertEqual(llm.__class__.__name__, "ChatGoogleGenerativeAI")
            
            print(f"Successfully initialized: {llm.__class__.__name__}")

if __name__ == "__main__":
    print("Testing Google AI Studio API Key configuration...")
    unittest.main()
