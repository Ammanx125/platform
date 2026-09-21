# app/services/retrieval/embeddings/mock.py
"""
Deterministic mock embeddings.

Same text always produces the same vector. Different texts produce different
vectors. Similar-looking texts (shared tokens) produce similar vectors,
enough to make the vector retriever behave plausibly in tests.

Not cryptographically useful. Not semantically meaningful. Just enough to
exercise the pipeline without a real embedding model.
"""
from __future__ import annotations

import hashlib
import math
import re

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _token_vector(token: str, dims: int) -> list[float]:
    """
    Deterministically map a token to a unit-ish vector.

    Uses SHA-256 of the token to seed a simple hash-based projection. Each
    of the N dimensions gets a value derived from a distinct slice of the
    hash. The result is L2-normalized so cosine similarity is well-behaved.
    """
    h = hashlib.sha256(token.encode("utf-8")).digest()
    # Repeat the digest to fill dims * 4 bytes of entropy (plenty).
    needed = dims * 4
    buf = (h * ((needed // len(h)) + 1))[:needed]
    vec = [
        int.from_bytes(buf[i * 4 : i * 4 + 4], "big") / 2**32 * 2.0 - 1.0
        for i in range(dims)
    ]
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        return vec
    return [v / norm for v in vec]


class MockEmbeddingProvider:
    dimensions: int

    def __init__(self, dimensions: int) -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        self.dimensions = dimensions

    async def embed(self, *, texts: list[str]) -> list[list[float]]:
        return [await self.embed_one(text=t) for t in texts]

    async def embed_one(self, *, text: str) -> list[float]:
        """
        Bag-of-tokens: sum of per-token unit vectors, then L2-normalize.

        Two texts with overlapping tokens end up closer in cosine distance
        than two texts with no overlap. That's the property the retriever
        tests rely on.
        """
        toks = _tokens(text)
        if not toks:
            # Zero-vector for empty input. Valid, just not retrievable.
            return [0.0] * self.dimensions

        acc = [0.0] * self.dimensions
        for tok in toks:
            tv = _token_vector(tok, self.dimensions)
            for i, v in enumerate(tv):
                acc[i] += v

        norm = math.sqrt(sum(v * v for v in acc))
        if norm == 0:
            return acc
        return [v / norm for v in acc]