"""
LLM & Embeddings Factory
------------------------
Provides two factory functions consumed across the agentguard package:

  get_llm()        -> BaseChatModel | None
  get_embeddings() -> Callable[[list[str]], list[list[float]]] | None

Provider resolution order for get_llm():
  1. Google AI Studio  (GOOGLE_API_KEY)
  2. Anthropic          (ANTHROPIC_API_KEY)
  3. OpenAI-compatible  (OPENAI_API_KEY)
       Set OPENAI_BASE_URL to redirect to any compatible endpoint.
       Example for OpenRouter: OPENAI_BASE_URL=https://openrouter.ai/api/v1
       Set OPENAI_MODEL to override the model name.
  4. Google Vertex AI   (GOOGLE_APPLICATION_CREDENTIALS)

Provider resolution order for get_embeddings():
  1. OpenAI  (OPENAI_API_KEY)  — text-embedding-3-small by default
       OPENAI_EMBEDDING_MODEL to override.
       OPENAI_BASE_URL respected (works with OpenRouter, LiteLLM, etc.)
  2. Google AI Studio  (GOOGLE_API_KEY)  — models/text-embedding-004
       GEMINI_EMBEDDING_MODEL to override.
  3. None → callers degrade to exact-match loop detection.
"""
import os
import logging
import hashlib
import json
from typing import Optional, Callable, Any
from langchain_core.language_models.chat_models import BaseChatModel

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)

_redis_client = None
_redis_initialized = False

def _get_redis():
    global _redis_client, _redis_initialized
    if not _redis_initialized:
        _redis_initialized = True
        try:
            import redis as redis_lib
            redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
            client = redis_lib.from_url(redis_url, decode_responses=True)
            client.ping()
            _redis_client = client
        except Exception as e:
            logger.debug(f"Redis cache not available for embeddings: {e}")
            _redis_client = None
    return _redis_client



def get_llm() -> Optional[BaseChatModel]:
    """
    Returns an initialized chat model based on available environment variables.
    Returns None if no provider is configured (triggers mock logic in callers).
    """

    # 1. Google AI Studio — preferred for Gemini without GCP service accounts
    if os.environ.get("GOOGLE_API_KEY"):
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
            model_name = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
            logger.debug(f"LLM: Google AI Studio ({model_name})")
            return ChatGoogleGenerativeAI(
                model=model_name,
                temperature=0,
                google_api_key=os.environ["GOOGLE_API_KEY"],
            )
        except ImportError:
            logger.warning("langchain-google-genai not installed. Skipping Google AI Studio.")

    # 2. Anthropic
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            from langchain_anthropic import ChatAnthropic
            model_name = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
            logger.debug(f"LLM: Anthropic ({model_name})")
            return ChatAnthropic(model=model_name, temperature=0)
        except ImportError:
            logger.warning("langchain-anthropic not installed. Skipping Anthropic.")

    # 3. OpenAI-compatible — works with OpenAI, OpenRouter, LiteLLM, vLLM, Ollama, etc.
    #    OPENAI_BASE_URL overrides the endpoint for any compatible provider.
    if os.environ.get("OPENAI_API_KEY"):
        try:
            from langchain_openai import ChatOpenAI
            model_name = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
            base_url = os.environ.get("OPENAI_BASE_URL")  # None = standard OpenAI
            logger.debug(f"LLM: OpenAI-compatible ({model_name}, base_url={base_url or 'default'})")
            return ChatOpenAI(
                model=model_name,
                temperature=0,
                api_key=os.environ["OPENAI_API_KEY"],
                **({"base_url": base_url} if base_url else {}),
            )
        except ImportError:
            logger.warning("langchain-openai not installed. Skipping OpenAI-compatible.")

    # 4. Google Vertex AI — requires GCP service account credentials on disk
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        try:
            from langchain_google_vertexai import ChatVertexAI
            model_name = os.environ.get("VERTEX_MODEL", "gemini-2.0-flash")
            logger.debug(f"LLM: Vertex AI ({model_name})")
            return ChatVertexAI(model=model_name, temperature=0)
        except ImportError:
            logger.warning("langchain-google-vertexai not installed. Skipping Vertex AI.")

    logger.warning("No LLM provider configured. Falling back to mock mode.")
    return None


def get_embeddings() -> Optional[Callable[[list], Optional[list]]]:
    """
    Returns an embedding callable: (texts: list[str]) -> list[list[float]] | None

    Uses cloud embedding APIs — no local container required.
    Returns None if no provider is configured; callers fall back to exact-match
    loop detection automatically.
    """

    base_embed_fn = None
    cache_namespace = ""  # provider:model[:dim] — keeps cached vectors from different embedders apart

    # 1. OpenAI embeddings (primary — text-embedding-3-small is fast and cheap)
    if os.environ.get("OPENAI_API_KEY") and base_embed_fn is None:
        try:
            from langchain_openai import OpenAIEmbeddings
            model = os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
            base_url = os.environ.get("OPENAI_BASE_URL")
            embedder = OpenAIEmbeddings(
                model=model,
                api_key=os.environ["OPENAI_API_KEY"],
                **({"base_url": base_url} if base_url else {}),
            )
            logger.debug(f"Embeddings: OpenAI ({model})")

            def embed_openai(texts: list) -> Optional[list]:
                try:
                    return embedder.embed_documents(texts)
                except Exception as e:
                    logger.error(f"OpenAI embeddings failed: {e}")
                    return None

            base_embed_fn = embed_openai
            cache_namespace = f"openai:{model}"
        except ImportError:
            logger.warning("langchain-openai not installed. Skipping OpenAI embeddings.")

    # 2. Google AI Studio embeddings (fallback — works with existing GOOGLE_API_KEY)
    if os.environ.get("GOOGLE_API_KEY") and base_embed_fn is None:
        try:
            from langchain_google_genai import GoogleGenerativeAIEmbeddings
            model = os.environ.get("GEMINI_EMBEDDING_MODEL", "models/gemini-embedding-001")
            # gemini-embedding-001 emits 3072 dims by default; truncate (MRL) to
            # EMBEDDING_DIM so vectors fit the pgvector schema (default 1536).
            output_dim = int(os.environ.get("EMBEDDING_DIM", "") or "1536")
            embedder = GoogleGenerativeAIEmbeddings(
                model=model,
                google_api_key=os.environ["GOOGLE_API_KEY"],
                output_dimensionality=output_dim,
            )
            logger.debug(f"Embeddings: Google AI Studio ({model}, dim={output_dim})")

            def embed_google(texts: list) -> Optional[list]:
                try:
                    return embedder.embed_documents(texts)
                except Exception as e:
                    logger.error(f"Google embeddings failed: {e}")
                    return None

            base_embed_fn = embed_google
            cache_namespace = f"google:{model}:{output_dim}"
        except ImportError:
            logger.warning("langchain-google-genai not installed. Skipping Google embeddings.")

    if base_embed_fn is None:
        logger.warning(
            "No embeddings provider configured. "
            "Semantic loop detection disabled — falling back to exact-match."
        )
        return None

    def cached_embed_fn(texts: list) -> Optional[list]:
        redis_client = _get_redis()
        if not redis_client:
            return base_embed_fn(texts)

        results = [None] * len(texts)
        uncached_indices = []
        uncached_texts = []

        for idx, text in enumerate(texts):
            text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            cache_key = f"embedding:v1:{cache_namespace}:{text_hash}"
            cached_val = redis_client.get(cache_key)
            if cached_val:
                results[idx] = json.loads(cached_val)
            else:
                uncached_indices.append((idx, cache_key))
                uncached_texts.append(text)

        if uncached_texts:
            new_embeddings = base_embed_fn(uncached_texts)
            if new_embeddings is None:
                return None  # Provider failed
            for (idx, cache_key), emb in zip(uncached_indices, new_embeddings):
                results[idx] = emb
                redis_client.setex(cache_key, 2592000, json.dumps(emb))  # 30 days TTL

        return results

    return cached_embed_fn


def normalize_llm_output(content: Any) -> str:
    """
    Standardizes LLM output from all providers into a plain string.
    Handles Gemini's list-of-parts format as well as plain strings.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    return str(content)
