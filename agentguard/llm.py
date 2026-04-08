import os
from typing import Optional, Any
from langchain_core.language_models.chat_models import BaseChatModel

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

def get_llm() -> Optional[BaseChatModel]:
    """
    Factory function to initialize the appropriate LLM based on environment variables.

    Supports:
    - Google AI Studio (GOOGLE_API_KEY) - Recommended for Gemini
    - Anthropic (ANTHROPIC_API_KEY)
    - OpenAI (OPENAI_API_KEY)
    - Google Vertex AI (GOOGLE_APPLICATION_CREDENTIALS)
    """
    # 1. Try Google AI Studio first (Gemini)
    if os.environ.get("GOOGLE_API_KEY"):
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
            # Use the specified GEMINI_MODEL or fall back to the modern 3.1 Pro standard
            model_name = os.environ.get("GEMINI_MODEL", "gemini-3.1-pro-preview")
            return ChatGoogleGenerativeAI(
                model=model_name, 
                temperature=0, 
                google_api_key=os.environ.get("GOOGLE_API_KEY")
            )
        except ImportError:
            pass

    # 2. Try Anthropic
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            from langchain_anthropic import ChatAnthropic
            # Use claude-3-haiku-20240307 as a default standard fast model
            return ChatAnthropic(model="claude-3-haiku-20240307", temperature=0)
        except ImportError:
            pass

    # 3. Try Vertex AI via Service Account
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        try:
            from langchain_google_vertexai import ChatVertexAI
            # Use gemini-1.5-flash as a default standard fast model
            return ChatVertexAI(model="gemini-1.5-flash", temperature=0)
        except ImportError:
            pass

    # 4. Fall back to OpenAI
    if os.environ.get("OPENAI_API_KEY"):
        try:
            from langchain_openai import ChatOpenAI
            # Use gpt-4o-mini as a default standard fast model
            return ChatOpenAI(model="gpt-4o-mini", temperature=0)
        except ImportError:
            pass

    # 5. No configuration found, return None (will use mock logic)
    return None

def normalize_llm_output(content: Any) -> str:
    """
    Standardizes LLM output from various providers (Google, OpenAI, Anthropic) 
    into a clean string. Handles cases where content might be a list of parts 
    (modern Gemini/Vertex format).
    """
    if content is None:
        return ""
    
    if isinstance(content, str):
        return content
    
    if isinstance(content, list):
        # Join text parts if it's a list of dicts or strings
        return "".join([
            part.get("text", "") if isinstance(part, dict) else str(part) 
            for part in content
        ])
    
    # Fallback for unexpected types
    return str(content)
