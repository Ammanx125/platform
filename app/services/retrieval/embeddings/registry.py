# app/services/retrieval/embeddings/registry.py
from __future__ import annotations

from app.core.config import settings
from app.services.retrieval.embeddings.base import EmbeddingProvider
from app.services.retrieval.embeddings.mock import MockEmbeddingProvider

_PROVIDER: EmbeddingProvider | None = None


def _build() -> EmbeddingProvider:
    provider = settings.embedding_provider.lower()
    if provider == "mock":
        return MockEmbeddingProvider(dimensions=settings.embedding_dimensions)
    raise ValueError(f"unknown embedding provider: {provider!r}")


def get_embedding_provider() -> EmbeddingProvider:
    global _PROVIDER
    if _PROVIDER is None:
        _PROVIDER = _build()
    return _PROVIDER


def reset_embedding_provider() -> None:
    """For tests that need to rebuild the provider after config change."""
    global _PROVIDER
    _PROVIDER = None