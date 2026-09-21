# tests/unit/retrieval/test_mock_embeddings.py
import math

import pytest

from app.services.retrieval.embeddings.mock import MockEmbeddingProvider


@pytest.mark.asyncio
async def test_deterministic() -> None:
    p = MockEmbeddingProvider(dimensions=64)
    v1 = await p.embed_one(text="hello world")
    v2 = await p.embed_one(text="hello world")
    assert v1 == v2


@pytest.mark.asyncio
async def test_different_texts_differ() -> None:
    p = MockEmbeddingProvider(dimensions=64)
    v1 = await p.embed_one(text="hello")
    v2 = await p.embed_one(text="goodbye")
    assert v1 != v2


@pytest.mark.asyncio
async def test_token_overlap_yields_similarity() -> None:
    p = MockEmbeddingProvider(dimensions=128)
    a = await p.embed_one(text="supplier acme bolt")
    b = await p.embed_one(text="supplier beacon nut")
    c = await p.embed_one(text="unrelated words here")

    def cosine(x, y):
        return sum(xi * yi for xi, yi in zip(x, y, strict=True))

    sim_ab = cosine(a, b)
    sim_ac = cosine(a, c)
    assert sim_ab > sim_ac


@pytest.mark.asyncio
async def test_batch_matches_single() -> None:
    p = MockEmbeddingProvider(dimensions=32)
    batch = await p.embed(texts=["a", "b"])
    single_a = await p.embed_one(text="a")
    single_b = await p.embed_one(text="b")
    assert batch[0] == single_a
    assert batch[1] == single_b


@pytest.mark.asyncio
async def test_norm_is_unit_for_nonempty() -> None:
    p = MockEmbeddingProvider(dimensions=64)
    v = await p.embed_one(text="hello world")
    norm = math.sqrt(sum(x * x for x in v))
    assert abs(norm - 1.0) < 1e-9