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
    print("\n--- Starting Google AI Studio Inference Test ---")
    
    # Ensure we are using the Google configuration
    if not os.environ.get("GOOGLE_API_KEY"):
        print("ERROR: GOOGLE_API_KEY not set in environment/.env")
        print("Go to https://aistudio.google.com/ to get one.")
        sys.exit(1)

    print("Initializing LLM...")
    
    # Force the environment to prioritize Google
    os.environ["ANTHROPIC_API_KEY"] = ""
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = ""
    os.environ["OPENAI_API_KEY"] = ""
    
    llm = get_llm()
    
    if llm is None:
        print("ERROR: Failed to initialize LLM (get_llm returned None)")
        sys.exit(1)
        
    print(f"LLM Class: {llm.__class__.__name__}")
    print("Sending prompt: 'Say hello and confirm you are running via Google AI Studio.'")
    
    try:
        response = llm.invoke("Say hello and confirm you are running via Google AI Studio.")
        print("\n--- Response ---")
        print(response.content)
        print("----------------\n")
        print("Inference successful!")
    except Exception as e:
        print(f"\nERROR during inference: {e}")
        sys.exit(1)

if __name__ == "__main__":
    test_inference()
