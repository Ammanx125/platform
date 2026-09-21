# tests/unit/retrieval/test_chunking.py
import pytest

from app.services.retrieval.chunking import chunk_text


def test_empty_text() -> None:
    assert chunk_text("") == []
    assert chunk_text("   ") == []


def test_short_text_single_chunk() -> None:
    text = " ".join(f"t{i}" for i in range(50))
    chunks = chunk_text(text, target_tokens=100, overlap_tokens=10)
    assert len(chunks) == 1
    assert chunks[0].ordinal == 0
    assert chunks[0].token_count == 50


def test_long_text_multiple_chunks() -> None:
    text = " ".join(f"t{i}" for i in range(1000))
    chunks = chunk_text(text, target_tokens=100, overlap_tokens=20)
    assert len(chunks) > 1
    # Ordinals are sequential
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_overlap_is_respected() -> None:
    text = " ".join(f"t{i}" for i in range(300))
    chunks = chunk_text(text, target_tokens=100, overlap_tokens=20)
    # Second chunk should start at token 80 (100 - 20)
    assert chunks[1].metadata["start_token"] == 80


def test_rejects_bad_overlap() -> None:
    with pytest.raises(ValueError):
        chunk_text("a b c", target_tokens=10, overlap_tokens=10)
    with pytest.raises(ValueError):
        chunk_text("a b c", target_tokens=10, overlap_tokens=-1)