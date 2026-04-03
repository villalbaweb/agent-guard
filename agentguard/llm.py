import os
from typing import Optional
from langchain_core.language_models.chat_models import BaseChatModel

def get_llm() -> Optional[BaseChatModel]:
    """
    Factory function to initialize the appropriate LLM based on environment variables.

    Supports:
    - OpenAI (OPENAI_API_KEY)
    - Anthropic (ANTHROPIC_API_KEY)
    - Google Vertex AI (GOOGLE_APPLICATION_CREDENTIALS or GOOGLE_API_KEY)
    """
    # 1. Try Anthropic first if API key is set
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            from langchain_anthropic import ChatAnthropic
            # Use claude-3-haiku-20240307 as a default standard fast model
            return ChatAnthropic(model="claude-3-haiku-20240307", temperature=0)
        except ImportError:
            pass

    # 2. Try Vertex AI first if Google credentials are set
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        try:
            from langchain_google_vertexai import ChatVertexAI
            # Use gemini-1.5-flash as a default standard fast model for routing/policy
            return ChatVertexAI(model="gemini-1.5-flash", temperature=0)
        except ImportError:
            pass

    # 3. Try Gemini with API key
    if os.environ.get("GOOGLE_API_KEY"):
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
            # Use gemini-1.5-flash as a default standard fast model for routing/policy
            return ChatGoogleGenerativeAI(model="gemini-1.5-flash", temperature=0)
        except ImportError:
            pass

    # 4. Fall back to OpenAI if API key is set
    if os.environ.get("OPENAI_API_KEY"):
        try:
            from langchain_openai import ChatOpenAI
            # Use gpt-4o-mini as a default standard fast model
            return ChatOpenAI(model="gpt-4o-mini", temperature=0)
        except ImportError:
            pass

    # 5. No configuration found, return None (will use mock logic)
    return None
