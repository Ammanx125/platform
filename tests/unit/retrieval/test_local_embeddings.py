# tests/unit/retrieval/test_local_embeddings.py
"""
Real-model tests. Marked slow — not run by default.

Run with: pytest -m slow tests/unit/retrieval/test_local_embeddings.py

The first run downloads intfloat/multilingual-e5-large (~2 GB) into
fastembed's cache. Subsequent runs are fast.
"""
from __future__ import annotations

import pytest

from app.services.retrieval.embeddings.local import LocalEmbeddingProvider

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def provider() -> LocalEmbeddingProvider:
    return LocalEmbeddingProvider(
        model_name="intfloat/multilingual-e5-large",
        dimensions=1024,
    )


@pytest.mark.asyncio
async def test_embed_one_returns_1024(provider):
    v = await provider.embed_one(text="hello world")
    assert len(v) == 1024
    assert all(isinstance(x, float) for x in v)


@pytest.mark.asyncio
async def test_embed_batch(provider):
    vs = await provider.embed(texts=["hello", "world", "goodbye"])
    assert len(vs) == 3
    assert all(len(v) == 1024 for v in vs)


@pytest.mark.asyncio
async def test_similar_texts_are_closer_than_dissimilar(provider):
    """
    The actual reason this provider exists: semantic similarity, not just
    token overlap. "a bolt and a nut" and "fasteners for assembly" share no
    tokens but should be closer than either is to "quarterly tax filing".
    """
    a = await provider.embed_one(text="a bolt and a nut")
    b = await provider.embed_one(text="fasteners for assembly")
    c = await provider.embed_one(text="quarterly tax filing")

    def cosine(x, y):
        return sum(xi * yi for xi, yi in zip(x, y, strict=True))

    sim_ab = cosine(a, b)
    sim_ac = cosine(a, c)
    assert sim_ab > sim_ac, (
        f"expected semantic similarity, got sim(a,b)={sim_ab}, sim(a,c)={sim_ac}"
    )


@pytest.mark.asyncio
async def test_multilingual(provider):
    """
    The multilingual claim: 'bolt' and 'bolt' in another language should be
    reasonably close, because that's what the model was trained for. Using
    German as a concrete example.
    """
    en = await provider.embed_one(text="a steel bolt")
    de = await provider.embed_one(text="eine Stahlschraube")   # steel bolt in German
    fr = await provider.embed_one(text="le droit fiscal")      # tax law in French — unrelated

    def cosine(x, y):
        return sum(xi * yi for xi, yi in zip(x, y, strict=True))

    assert cosine(en, de) > cosine(en, fr)


@pytest.mark.asyncio
async def test_empty_batch(provider):
    assert await provider.embed(texts=[]) == []