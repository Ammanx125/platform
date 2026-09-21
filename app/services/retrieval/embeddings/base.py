# app/services/retrieval/embeddings/base.py
from __future__ import annotations

from typing import Protocol


class EmbeddingProvider(Protocol):
    """
    Contract for text-to-vector embedding.

    Implementations:
      - MockEmbeddingProvider (this step): deterministic, hash-based.
      - OpenAIEmbeddingProvider / VoyageEmbeddingProvider / local sentence-
        transformers (later, when real embeddings are needed).

    The rest of Sansa (chunking service, retrievers) never imports a concrete
    provider; it goes through get_embedding_provider().
    """
    dimensions: int

    async def embed(self, *, texts: list[str]) -> list[list[float]]: ...

    async def embed_one(self, *, text: str) -> list[float]: ...