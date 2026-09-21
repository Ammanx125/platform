# app/services/retrieval/embeddings/local.py
"""
Self-hosted embedding provider backed by fastembed (ONNX).

Default model: intfloat/multilingual-e5-large (1024 dims, 100+ languages).

Design notes:
  - The model is loaded LAZILY on first embed() call, not at import. This
    keeps app startup fast and lets tests that use MockEmbeddingProvider
    avoid paying the model-load cost entirely.
  - Model download happens once on first use; fastembed caches it under
    fastembed_cache_dir (or its own default). Subsequent processes reuse
    the cached files.
  - All model work runs off the event loop via asyncio.to_thread, because
    fastembed's API is synchronous and CPU-bound.
  - E5-family models were trained with "query: " and "passage: " prefixes.
    We apply the prefix automatically so callers don't have to know. This
    matches the model card's usage and is required for good retrieval
    quality — omitting it materially degrades similarity scores.
"""
from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastembed import TextEmbedding


# E5-family prefix convention. BGE-M3 and other models don't use prefixes,
# but since we default to e5-large, applying them is correct. If you later
# switch to a model that doesn't want prefixes, make these empty strings.
_QUERY_PREFIX = "query: "
_PASSAGE_PREFIX = "passage: "


class LocalEmbeddingProvider:
    """
    Self-hosted embedding via fastembed.

    Thread-safe lazy loading: the first caller to embed() blocks while the
    model loads; concurrent callers wait on the same lock and then proceed.
    """

    dimensions: int

    def __init__(
        self,
        *,
        model_name: str,
        dimensions: int,
        cache_dir: str | None = None,
        batch_size: int = 32,
    ) -> None:
        self._model_name = model_name
        self.dimensions = dimensions
        self._cache_dir = cache_dir
        self._batch_size = batch_size
        self._model: TextEmbedding | None = None
        self._load_lock = threading.Lock()

    def _ensure_model(self) -> TextEmbedding:
        """
        Load the model if it isn't loaded. Runs synchronously — callers
        should wrap with asyncio.to_thread.
        """
        if self._model is not None:
            return self._model
        with self._load_lock:
            # Re-check under lock: another thread may have loaded it.
            if self._model is not None:
                return self._model
            from fastembed import TextEmbedding
            kwargs: dict = {"model_name": self._model_name}
            if self._cache_dir:
                kwargs["cache_dir"] = self._cache_dir
            self._model = TextEmbedding(**kwargs)
        return self._model

    def _embed_sync(self, *, texts: list[str], prefix: str) -> list[list[float]]:
        model = self._ensure_model()
        prefixed = [f"{prefix}{t}" for t in texts]
        # fastembed returns a generator of np.ndarray; convert to lists.
        vectors = [v.tolist() for v in model.embed(prefixed, batch_size=self._batch_size)]
        return vectors

    async def embed(self, *, texts: list[str]) -> list[list[float]]:
        """
        Embed a batch of passages.

        Empty input returns an empty list — no model call, no error.
        """
        if not texts:
            return []
        return await asyncio.to_thread(
            self._embed_sync, texts=texts, prefix=_PASSAGE_PREFIX
        )

    async def embed_one(self, *, text: str) -> list[float]:
        """
        Embed a single query.

        Uses the query prefix, not the passage prefix. This is the E5
        convention: queries and passages live in the same vector space but
        are encoded with different prefixes to improve retrieval.
        """
        vectors = await asyncio.to_thread(
            self._embed_sync, texts=[text], prefix=_QUERY_PREFIX
        )
        return vectors[0]