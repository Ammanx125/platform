# app/services/llm/registry.py
from __future__ import annotations

from app.core.config import settings
from app.services.llm.base import LLMProvider
from app.services.llm.gemini import GeminiProvider
from app.services.llm.mock import MockLLMProvider
from app.services.llm.sglang import SGLangProvider

_PROVIDER: LLMProvider | None = None


def _build() -> LLMProvider:
    provider = settings.llm_provider.lower()
    if provider == "mock":
        return MockLLMProvider()
    if provider == "sglang":
        return SGLangProvider()
    if provider == "gemini":
        return GeminiProvider()
    raise ValueError(
        f"unknown LLM provider: {provider!r}; expected 'mock', 'sglang', or 'gemini'"
    )


def get_llm_provider() -> LLMProvider:
    global _PROVIDER
    if _PROVIDER is None:
        _PROVIDER = _build()
    return _PROVIDER


def reset_llm_provider() -> None:
    """For tests that need to rebuild the provider after config change."""
    global _PROVIDER
    _PROVIDER = None