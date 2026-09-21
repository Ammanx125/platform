# tests/unit/retrieval/test_chunking_blocks.py
import pytest

from app.services.retrieval.blocks import Block
from app.services.retrieval.chunking import chunk_blocks


def _p(text: str, **meta) -> Block:
    return Block(text=text, kind="paragraph", metadata=meta)


def _h(text: str, level: int = 1) -> Block:
    return Block(text=text, kind="heading", metadata={"level": level})


def test_empty_input():
    assert chunk_blocks([]) == []


def test_single_short_block():
    chunks = chunk_blocks([_p("hello world")], target_tokens=100, overlap_tokens=10)
    assert len(chunks) == 1
    assert chunks[0].text == "hello world"
    assert chunks[0].ordinal == 0
    assert chunks[0].metadata["block_kinds"] == ["paragraph"]


def test_blocks_merge_when_under_target():
    blocks = [_p("a b c"), _p("d e f"), _p("g h i")]
    chunks = chunk_blocks(blocks, target_tokens=100, overlap_tokens=10)
    assert len(chunks) == 1
    assert chunks[0].text == "a b c\n\nd e f\n\ng h i"
    assert chunks[0].metadata["block_kinds"] == ["paragraph"] * 3


def test_blocks_split_at_target():
    blocks = [_p("word " * 30) for _ in range(5)]
    chunks = chunk_blocks(blocks, target_tokens=60, overlap_tokens=10)
    assert len(chunks) > 1


def test_heading_sticks_to_following_content():
    blocks = [
        _p("intro paragraph that came first"),
        _h("Payment Terms"),
        _p("the supplier shall pay"),
    ]
    chunks = chunk_blocks(blocks, target_tokens=100, overlap_tokens=10)
    # The heading should be in the same chunk as "the supplier shall pay",
    # not the same chunk as "intro paragraph".
    heading_chunk = next(c for c in chunks if "Payment Terms" in c.text)
    assert "the supplier shall pay" in heading_chunk.text
    assert "intro paragraph" not in heading_chunk.text


def test_heading_prefix_applied():
    blocks = [_h("Payment Terms"), _p("some content")]
    chunks = chunk_blocks(
        blocks, target_tokens=100, overlap_tokens=10, heading_prefix=True
    )
    assert chunks[0].text.startswith("Payment Terms: ")


def test_heading_prefix_disabled():
    blocks = [_h("Payment Terms"), _p("some content")]
    chunks = chunk_blocks(
        blocks, target_tokens=100, overlap_tokens=10, heading_prefix=False
    )
    assert not chunks[0].text.startswith("Payment Terms: ")


def test_nested_headings_stack():
    blocks = [
        _h("Section 3", level=1),
        _h("Payment Terms", level=2),
        _p("body text here"),
    ]
    chunks = chunk_blocks(
        blocks, target_tokens=100, overlap_tokens=10, heading_prefix=True
    )
    assert chunks[0].text.startswith("Section 3 > Payment Terms: ")


def test_heading_stack_pops_on_same_level():
    blocks = [
        _h("Section A", level=2),
        _p("body A"),
        _h("Section B", level=2),
        _p("body B"),
    ]
    chunks = chunk_blocks(
        blocks, target_tokens=1000, overlap_tokens=10, heading_prefix=True
    )
    # Two chunks, because heading B forces an emit of A's chunk.
    texts = [c.text for c in chunks]
    assert any("Section A:" in t and "body A" in t for t in texts)
    assert any("Section B:" in t and "body B" in t for t in texts)


def test_oversized_block_split_on_sentences():
    long_text = " ".join(f"Sentence {i}." for i in range(50))
    blocks = [_p(long_text)]
    chunks = chunk_blocks(blocks, target_tokens=20, overlap_tokens=5)
    assert len(chunks) > 1
    # Each chunk should end with terminal punctuation from a sentence.
    for c in chunks:
        assert c.text.rstrip().endswith(".")


def test_strategy_strict_no_overlap():
    blocks = [_p("alpha " * 30), _p("beta " * 30)]
    chunks = chunk_blocks(
        blocks, target_tokens=30, overlap_tokens=5, strategy="strict"
    )
    # Under strict, the same paragraph should not appear in two chunks.
    seen = set()
    for c in chunks:
        for word in ("alpha", "beta"):
            if word in c.text:
                assert word not in seen, f"{word} appears in two chunks"
                seen.add(word)


def test_strategy_continuity_repeats_last_block():
    blocks = [_p("alpha " * 30), _p("beta " * 30)]
    chunks = chunk_blocks(
        blocks, target_tokens=30, overlap_tokens=10, strategy="continuity"
    )
    tokens_chunk_0 = set(chunks[0].text.split())
    tokens_chunk_1 = set(chunks[1].text.split())
    assert tokens_chunk_0 & tokens_chunk_1


def test_continuity_skips_large_last_block():
    # Last block is 200 tokens, overlap is 10 -> 10 * 3 = 30. Should not repeat.
    big = _p(" ".join(f"big-{i}" for i in range(200)))
    small = _p("small " * 5)
    blocks = [small, big]
    chunks = chunk_blocks(
        blocks, target_tokens=100, overlap_tokens=10, strategy="continuity"
    )
    big_chunks = [set(c.text.split()) for c in chunks if "big-" in c.text]
    assert len(big_chunks) == 2
    assert big_chunks[0].isdisjoint(big_chunks[1])


def test_page_break_resets_continuity():
    blocks = [
        _p("before the break " * 20),
        Block(text="", kind="page_break", metadata={}),
        _p("after the break " * 20),
    ]
    chunks = chunk_blocks(
        blocks, target_tokens=40, overlap_tokens=10, strategy="continuity"
    )
    # No chunk should contain both "before" and "after" content.
    for c in chunks:
        assert not ("before" in c.text and "after" in c.text)


def test_page_break_preserves_heading_stack():
    blocks = [
        _h("Section 1", level=1),
        _p("before page"),
        Block(text="", kind="page_break", metadata={}),
        _p("after page"),
    ]
    chunks = chunk_blocks(
        blocks, target_tokens=1000, overlap_tokens=10, heading_prefix=True
    )
    # Both chunks should still carry the Section 1 prefix.
    for c in chunks:
        if "after page" in c.text or "before page" in c.text:
            assert "Section 1" in c.text


def test_invalid_strategy_rejected():
    with pytest.raises(ValueError):
        chunk_blocks([_p("x")], strategy="nonsense")