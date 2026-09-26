# tests/unit/llm/test_registry.py
import pytest

from app.core.config import settings
from app.services.llm import registry
from app.services.llm.mock import MockLLMProvider
from app.services.llm.sglang import SGLangProvider


@pytest.fixture(autouse=True)
def _reset():
    registry.reset_llm_provider()
    yield
    registry.reset_llm_provider()


def test_mock_default(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "mock", raising=False)
    p = registry.get_llm_provider()
    assert isinstance(p, MockLLMProvider)


def test_sglang_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "sglang", raising=False)
    p = registry.get_llm_provider()
    # Building the provider must not attempt a request; only `.generate()`
    # does that. So this test proves construction is safe.
    assert isinstance(p, SGLangProvider)


def test_unknown_raises(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "gpt", raising=False)
    with pytest.raises(ValueError, match="unknown LLM provider"):
        registry.get_llm_provider()


def test_singleton(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "mock", raising=False)
    assert registry.get_llm_provider() is registry.get_llm_provider()