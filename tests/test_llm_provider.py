"""
tests/test_llm_provider.py — chat-provider selection logic
-------------------------------------------------------------
Verifies get_llm() / get_active_llm_provider():
  - explicit LLM_PROVIDER selection (google, openrouter, ...)
  - explicit selection wins over auto-detect priority
  - misconfigured explicit selection returns None (mock mode), never another provider
  - auto-detect priority order when LLM_PROVIDER is unset
  - OpenRouter model/base_url wiring

No network calls: constructing the langchain chat models only validates config.
"""
import os
import pytest
from unittest.mock import patch

from agentguard.llm import get_llm, get_active_llm_provider

_ALL_PROVIDER_VARS = {
    "LLM_PROVIDER": "",
    "GOOGLE_API_KEY": "",
    "ANTHROPIC_API_KEY": "",
    "OPENROUTER_API_KEY": "",
    "OPENROUTER_MODEL": "",
    "OPENROUTER_BASE_URL": "",
    "OPENAI_API_KEY": "",
    "GOOGLE_APPLICATION_CREDENTIALS": "",
}


def _env(**overrides):
    """Context manager: clear all provider vars, then apply overrides."""
    merged = dict(_ALL_PROVIDER_VARS)
    merged.update(overrides)
    return patch.dict(os.environ, merged, clear=False)


class TestExplicitSelection:
    def test_explicit_google(self):
        with _env(LLM_PROVIDER="google", GOOGLE_API_KEY="test-key"):
            llm = get_llm()
            assert llm is not None
            assert llm.__class__.__name__ == "ChatGoogleGenerativeAI"
            assert get_active_llm_provider() == "google"

    def test_gemini_alias(self):
        with _env(LLM_PROVIDER="gemini", GOOGLE_API_KEY="test-key"):
            llm = get_llm()
            assert llm is not None
            assert llm.__class__.__name__ == "ChatGoogleGenerativeAI"

    def test_explicit_openrouter(self):
        with _env(
            LLM_PROVIDER="openrouter",
            OPENROUTER_API_KEY="sk-or-test",
            OPENROUTER_MODEL="anthropic/claude-haiku-4.5",
        ):
            llm = get_llm()
            assert llm is not None
            assert llm.__class__.__name__ == "ChatOpenAI"
            assert llm.model_name == "anthropic/claude-haiku-4.5"
            assert "openrouter.ai" in str(llm.openai_api_base)
            assert get_active_llm_provider() == "openrouter"

    def test_explicit_wins_over_auto_priority(self):
        """openrouter must be used even though GOOGLE_API_KEY is also set."""
        with _env(
            LLM_PROVIDER="openrouter",
            GOOGLE_API_KEY="google-key",
            OPENROUTER_API_KEY="sk-or-test",
        ):
            llm = get_llm()
            assert llm is not None
            assert llm.__class__.__name__ == "ChatOpenAI"

    def test_explicit_provider_missing_key_returns_none(self):
        """Never silently fall back to a different provider."""
        with _env(LLM_PROVIDER="openrouter", GOOGLE_API_KEY="google-key"):
            assert get_llm() is None

    def test_unknown_provider_returns_none(self):
        with _env(LLM_PROVIDER="nonsense", GOOGLE_API_KEY="google-key"):
            assert get_llm() is None
            assert get_active_llm_provider() is None


class TestAutoDetect:
    def test_google_first(self):
        with _env(GOOGLE_API_KEY="g", OPENROUTER_API_KEY="o", OPENAI_API_KEY="oa"):
            llm = get_llm()
            assert llm.__class__.__name__ == "ChatGoogleGenerativeAI"
            assert get_active_llm_provider() == "google"

    def test_openrouter_before_openai(self):
        with _env(OPENROUTER_API_KEY="sk-or-test", OPENAI_API_KEY="sk-test"):
            llm = get_llm()
            assert llm.__class__.__name__ == "ChatOpenAI"
            assert "openrouter.ai" in str(llm.openai_api_base)
            assert get_active_llm_provider() == "openrouter"

    def test_no_keys_returns_none(self):
        with _env():
            assert get_llm() is None
            assert get_active_llm_provider() is None


class TestOpenRouterConfig:
    def test_default_model(self):
        with _env(LLM_PROVIDER="openrouter", OPENROUTER_API_KEY="sk-or-test"):
            llm = get_llm()
            assert llm.model_name == "openai/gpt-4o-mini"

    def test_custom_base_url(self):
        with _env(
            LLM_PROVIDER="openrouter",
            OPENROUTER_API_KEY="sk-or-test",
            OPENROUTER_BASE_URL="http://localhost:4000/v1",
        ):
            llm = get_llm()
            assert "localhost:4000" in str(llm.openai_api_base)
