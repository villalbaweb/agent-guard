import os
import sys
import logging

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from agentguard.llm import get_llm

# Configure logging to see what's happening
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_inference():
    print("\n--- Starting Vertex AI Inference Test ---")
    
    # Ensure we are using the Vertex configuration
    # Note: We don't mock here because we want to use the real .env values
    if not os.environ.get("VERTEX_API_KEY") or not os.environ.get("VERTEX_PROJECT_ID"):
        print("ERROR: VERTEX_API_KEY or VERTEX_PROJECT_ID not set in environment/.env")
        sys.exit(1)

    print(f"Project ID: {os.environ.get('VERTEX_PROJECT_ID')}")
    print("Initializing LLM...")
    
    # Force the environment to prioritize Vertex by clearing others if they exist in .env
    # This ensures get_llm() picks the Vertex path
    os.environ["ANTHROPIC_API_KEY"] = ""
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = ""
    
    llm = get_llm()
    
    if llm is None:
        print("ERROR: Failed to initialize LLM (get_llm returned None)")
        sys.exit(1)
        
    print(f"LLM Class: {llm.__class__.__name__}")
    print("Sending prompt: 'Say hello and confirm you are running via Vertex AI.'")
    
    try:
        response = llm.invoke("Say hello and confirm you are running via Vertex AI.")
        print("\n--- Response ---")
        print(response.content)
        print("----------------\n")
        print("Inference successful!")
    except Exception as e:
        print(f"\nERROR during inference: {e}")
        sys.exit(1)

if __name__ == "__main__":
    test_inference()
