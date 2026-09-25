"""
LLM & Embeddings Factory
------------------------
Provides two factory functions consumed across the agentguard package:

  get_llm()        -> BaseChatModel | None
  get_embeddings() -> Callable[[list[str]], list[list[float]]] | None

Plus get_jev(use_case) -> JevClient | None for the decision points that Jev
(TypeSafe's typed-decision model) answers better than a chat model.

Chat provider selection for get_llm():
  Explicit  — set LLM_PROVIDER to one of:
                google | openrouter | anthropic | openai | vertex
              This wins even when several API keys are configured, making it
              the switch between e.g. Gemini and OpenRouter.
  Auto      — when LLM_PROVIDER is unset, first configured key wins, in order:
                1. Google AI Studio  (GOOGLE_API_KEY,        GEMINI_MODEL)
                2. Anthropic          (ANTHROPIC_API_KEY,    ANTHROPIC_MODEL)
                3. OpenRouter         (OPENROUTER_API_KEY,   OPENROUTER_MODEL)
                4. OpenAI-compatible  (OPENAI_API_KEY,       OPENAI_MODEL,
                                       OPENAI_BASE_URL for LiteLLM/vLLM/Ollama)
                5. Google Vertex AI   (GOOGLE_APPLICATION_CREDENTIALS)

  OpenRouter is multi-model: OPENROUTER_MODEL takes any slug from
  https://openrouter.ai/models (e.g. "anthropic/claude-haiku-4.5").

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



# --------------------------------------------------------------------------- #
#  Chat-model provider factories
#  Each returns a configured BaseChatModel, or None when its key/package is
#  missing.  get_llm() picks one — explicitly via LLM_PROVIDER, or by the
#  auto-detect priority order below.
# --------------------------------------------------------------------------- #

def _llm_google(model_override: Optional[str] = None) -> Optional[BaseChatModel]:
    """Google AI Studio — Gemini without GCP service accounts."""
    if not os.environ.get("GOOGLE_API_KEY"):
        return None
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
    except ImportError:
        logger.warning("langchain-google-genai not installed. Skipping Google AI Studio.")
        return None
    model_name = model_override or os.environ.get("GEMINI_MODEL") or "gemini-2.0-flash"
    logger.debug(f"LLM: Google AI Studio ({model_name})")
    return ChatGoogleGenerativeAI(
        model=model_name,
        temperature=0,
        google_api_key=os.environ["GOOGLE_API_KEY"],
    )


def _llm_openrouter(model_override: Optional[str] = None) -> Optional[BaseChatModel]:
    """OpenRouter — single API key, any model on https://openrouter.ai/models.

    OPENROUTER_MODEL takes the full slug, e.g. "anthropic/claude-haiku-4.5",
    "google/gemini-2.0-flash-001", "meta-llama/llama-3.3-70b-instruct".
    """
    if not os.environ.get("OPENROUTER_API_KEY"):
        return None
    try:
        from langchain_openai import ChatOpenAI
    except ImportError:
        logger.warning("langchain-openai not installed. Skipping OpenRouter.")
        return None
    # "or" defaults: empty-string values in .env count as unset
    model_name = model_override or os.environ.get("OPENROUTER_MODEL") or "openai/gpt-4o-mini"
    logger.debug(f"LLM: OpenRouter ({model_name})")
    return ChatOpenAI(
        model=model_name,
        temperature=0,
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url=os.environ.get("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1",
        default_headers={  # optional attribution headers recommended by OpenRouter
            "HTTP-Referer": "https://github.com/villalbaweb/agent-guard",
            "X-Title": "AgentGuard",
        },
    )


def _llm_anthropic(model_override: Optional[str] = None) -> Optional[BaseChatModel]:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        from langchain_anthropic import ChatAnthropic
    except ImportError:
        logger.warning("langchain-anthropic not installed. Skipping Anthropic.")
        return None
    model_name = model_override or os.environ.get("ANTHROPIC_MODEL") or "claude-haiku-4-5-20251001"
    logger.debug(f"LLM: Anthropic ({model_name})")
    return ChatAnthropic(model=model_name, temperature=0)


def _llm_openai(model_override: Optional[str] = None) -> Optional[BaseChatModel]:
    """OpenAI — or any OpenAI-compatible endpoint via OPENAI_BASE_URL
    (LiteLLM, vLLM, Ollama, ...)."""
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    try:
        from langchain_openai import ChatOpenAI
    except ImportError:
        logger.warning("langchain-openai not installed. Skipping OpenAI-compatible.")
        return None
    model_name = model_override or os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"
    base_url = os.environ.get("OPENAI_BASE_URL") or None  # None = standard OpenAI
    logger.debug(f"LLM: OpenAI-compatible ({model_name}, base_url={base_url or 'default'})")
    return ChatOpenAI(
        model=model_name,
        temperature=0,
        api_key=os.environ["OPENAI_API_KEY"],
        **({"base_url": base_url} if base_url else {}),
    )


def _llm_vertex(model_override: Optional[str] = None) -> Optional[BaseChatModel]:
    """Google Vertex AI — requires GCP service account credentials on disk."""
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        return None
    try:
        from langchain_google_vertexai import ChatVertexAI
    except ImportError:
        logger.warning("langchain-google-vertexai not installed. Skipping Vertex AI.")
        return None
    model_name = model_override or os.environ.get("VERTEX_MODEL") or "gemini-2.0-flash"
    logger.debug(f"LLM: Vertex AI ({model_name})")
    return ChatVertexAI(model=model_name, temperature=0)


_LLM_PROVIDERS = {
    "google": _llm_google,
    "gemini": _llm_google,        # alias
    "openrouter": _llm_openrouter,
    "anthropic": _llm_anthropic,
    "openai": _llm_openai,
    "vertex": _llm_vertex,
}

# Auto-detect order when LLM_PROVIDER is not set (backward compatible).
_AUTO_DETECT_ORDER = ("google", "anthropic", "openrouter", "openai", "vertex")


def get_active_llm_provider() -> Optional[str]:
    """Return the provider name get_llm() would use, or None if unconfigured."""
    choice = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if choice:
        return choice if choice in _LLM_PROVIDERS else None
    for name in _AUTO_DETECT_ORDER:
        if _LLM_PROVIDERS[name]() is not None:
            return name
    return None


def get_llm(model: Optional[str] = None) -> Optional[BaseChatModel]:
    """
    Returns an initialized chat model.

    Selection:
      1. Explicit — LLM_PROVIDER env var ("google" | "openrouter" | "anthropic"
         | "openai" | "vertex").  Misconfiguration logs an error and returns
         None (mock mode) rather than silently using a different provider.
      2. Auto-detect — first provider in _AUTO_DETECT_ORDER whose API key is set.

    `model` overrides the provider's configured model slug while keeping the
    same provider and credentials — see get_guard_llm().

    Returns None if no provider is configured (triggers mock logic in callers).
    """
    choice = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if choice:
        factory = _LLM_PROVIDERS.get(choice)
        if factory is None:
            logger.error(
                f"LLM_PROVIDER='{choice}' is not recognized. "
                f"Valid values: {sorted(set(_LLM_PROVIDERS))}. Falling back to mock mode."
            )
            return None
        llm = factory(model)
        if llm is None:
            logger.error(
                f"LLM_PROVIDER='{choice}' selected but its API key or package is "
                "missing. Falling back to mock mode."
            )
        return llm

    for name in _AUTO_DETECT_ORDER:
        llm = _LLM_PROVIDERS[name](model)
        if llm is not None:
            return llm

    logger.warning("No LLM provider configured. Falling back to mock mode.")
    return None


def get_guard_llm() -> Optional[BaseChatModel]:
    """Returns the model used for the PolicyEngine's semantic safety check.

    The guard runs on every step and answers with a single word, so it wants a
    fast, cheap model — while the executor's agents want the strongest one
    available. AGENTGUARD_GUARD_MODEL splits the two; without it the guard just
    shares the main model, as it always has.

    Latency here is not cosmetic: check_step fails closed, so a guard slow
    enough to time out its caller stalls or blocks real work.
    """
    guard_model = os.environ.get("AGENTGUARD_GUARD_MODEL") or None
    if guard_model:
        logger.info(f"PolicyEngine guard model: {guard_model} (AGENTGUARD_GUARD_MODEL)")
    return get_llm(guard_model)


# Decision points where Jev replaces a one-word chat-model prompt.  Generative
# steps (decompose, synthesize) are deliberately absent: Jev returns no text.
JEV_USE_CASES = ("guard", "reflect", "route")


def get_jev_use_cases() -> list:
    """Use cases enabled via AGENTGUARD_JEV ("guard,reflect,route" or "all")."""
    raw = os.environ.get("AGENTGUARD_JEV", "").strip().lower()
    if raw == "all":
        return list(JEV_USE_CASES)
    enabled = [u.strip() for u in raw.split(",") if u.strip()]
    unknown = sorted(set(enabled) - set(JEV_USE_CASES))
    if unknown:
        logger.error(f"AGENTGUARD_JEV: unknown use case(s) {unknown}. Valid: {list(JEV_USE_CASES)}.")
    return [u for u in JEV_USE_CASES if u in enabled]


def get_jev(use_case: str):
    """Returns a JevClient for `use_case`, or None to keep the chat-model path.

    The Jev counterpart of get_guard_llm(): the guard, reflection, and routing
    steps each ask a closed question, which Jev answers with a calibrated
    probability instead of a word the caller has to parse.  Opt-in per use
    case through AGENTGUARD_JEV so each can be rolled out (or rolled back)
    independently; unset, nothing changes.

    Jev is served by OpenRouter's Decisions API, so it reuses
    OPENROUTER_API_KEY whatever LLM_PROVIDER the chat model uses.
      AGENTGUARD_JEV_MODEL    model slug   (default typesafe/jev-1.13)
      AGENTGUARD_JEV_URL      endpoint     (default OpenRouter /api/alpha/decisions)
      AGENTGUARD_JEV_TIMEOUT  seconds      (default 10)
    """
    if use_case not in JEV_USE_CASES:
        raise ValueError(f"Unknown Jev use case '{use_case}'. Valid: {list(JEV_USE_CASES)}.")
    if use_case not in get_jev_use_cases():
        return None
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        logger.error(
            f"AGENTGUARD_JEV enables '{use_case}' but OPENROUTER_API_KEY is not set. "
            "Using the chat-model path instead."
        )
        return None

    from .jev import JevClient, DEFAULT_MODEL, DEFAULT_URL
    client = JevClient(
        api_key=api_key,
        model=os.environ.get("AGENTGUARD_JEV_MODEL") or DEFAULT_MODEL,
        url=os.environ.get("AGENTGUARD_JEV_URL") or DEFAULT_URL,
        timeout=float(os.environ.get("AGENTGUARD_JEV_TIMEOUT") or 10),
    )
    logger.info(f"Jev active for '{use_case}' ({client.model})")
    return client


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
