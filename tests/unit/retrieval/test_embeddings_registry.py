# tests/unit/retrieval/test_embeddings_registry.py
import pytest

from app.core.config import settings
from app.services.retrieval.embeddings import registry
from app.services.retrieval.embeddings.local import LocalEmbeddingProvider
from app.services.retrieval.embeddings.mock import MockEmbeddingProvider


@pytest.fixture(autouse=True)
def _reset_registry():
    registry.reset_embedding_provider()
    yield
    registry.reset_embedding_provider()


def test_builds_mock_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "embedding_provider", "mock", raising=False)
    provider = registry.get_embedding_provider()
    assert isinstance(provider, MockEmbeddingProvider)
    assert provider.dimensions == settings.embedding_dimensions


def test_builds_local_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "embedding_provider", "local", raising=False)
    provider = registry.get_embedding_provider()
    # LocalEmbeddingProvider loads its model lazily — instantiating it is
    # fast and doesn't download anything.
    assert isinstance(provider, LocalEmbeddingProvider)
    assert provider.dimensions == settings.embedding_dimensions


def test_unknown_provider_raises(monkeypatch):
    monkeypatch.setattr(settings, "embedding_provider", "wat", raising=False)
    with pytest.raises(ValueError, match="unknown embedding provider"):
        registry.get_embedding_provider()


def test_singleton(monkeypatch):
    monkeypatch.setattr(settings, "embedding_provider", "mock", raising=False)
    a = registry.get_embedding_provider()
    b = registry.get_embedding_provider()
    assert a is b