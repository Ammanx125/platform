# app/services/retrieval/embeddings/registry.py
from __future__ import annotations

from app.core.config import settings
from app.services.retrieval.embeddings.base import EmbeddingProvider
from app.services.retrieval.embeddings.local import LocalEmbeddingProvider
from app.services.retrieval.embeddings.mock import MockEmbeddingProvider

_PROVIDER: EmbeddingProvider | None = None


def _build() -> EmbeddingProvider:
    provider = settings.embedding_provider.lower()
    if provider == "local":
        return LocalEmbeddingProvider(
            model_name=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
            cache_dir=settings.fastembed_cache_dir,
            batch_size=settings.embedding_batch_size,
        )
    if provider == "mock":
        return MockEmbeddingProvider(dimensions=settings.embedding_dimensions)
    raise ValueError(
        f"unknown embedding provider: {provider!r}; expected 'local' or 'mock'"
    )


def get_embedding_provider() -> EmbeddingProvider:
    global _PROVIDER
    if _PROVIDER is None:
        _PROVIDER = _build()
    return _PROVIDER


def reset_embedding_provider() -> None:
    """
    For tests that need to rebuild the provider after a config change.

    Note: rebuilding a LocalEmbeddingProvider will discard the loaded model.
    The next embed() call reloads it from fastembed's on-disk cache (fast).
    """
    global _PROVIDER
    _PROVIDER = None