# tests/unit/retrieval/test_blocks.py
from app.services.retrieval.blocks import (
    Block,
    split_into_paragraphs,
    split_into_sentences,
)


def test_split_paragraphs_basic():
    text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    blocks = split_into_paragraphs(text)
    assert [b.kind for b in blocks] == ["paragraph"] * 3
    assert blocks[0].text == "First paragraph."
    assert blocks[2].text == "Third paragraph."


def test_split_paragraphs_drops_empty():
    text = "\n\n\nOnly one.\n\n\n\n"
    blocks = split_into_paragraphs(text)
    assert len(blocks) == 1
    assert blocks[0].text == "Only one."


def test_split_paragraphs_empty_input():
    assert split_into_paragraphs("") == []
    assert split_into_paragraphs("   \n\n   ") == []


def test_split_paragraphs_preserves_internal_newlines():
    text = "Line one\nLine two\n\nSeparate para"
    blocks = split_into_paragraphs(text)
    assert len(blocks) == 2
    assert "\n" in blocks[0].text


def test_split_paragraphs_collapses_inline_whitespace():
    text = "Too    many     spaces"
    blocks = split_into_paragraphs(text)
    assert blocks[0].text == "Too many spaces"


def test_split_sentences_basic():
    text = "One. Two! Three? Four."
    sents = split_into_sentences(text)
    assert sents == ["One.", "Two!", "Three?", "Four."]


def test_split_sentences_quotes_and_parens():
    text = 'He said "stop." Then he left. (Finally.)'
    sents = split_into_sentences(text)
    assert len(sents) == 3


def test_split_sentences_no_boundary():
    text = "no punctuation here"
    sents = split_into_sentences(text)
    assert sents == ["no punctuation here"]


def test_split_sentences_empty():
    assert split_into_sentences("") == []
    assert split_into_sentences("   ") == []


def test_block_helpers():
    h = Block(text="Payment Terms", kind="heading", metadata={"level": 2})
    p = Block(text="Some body.", kind="paragraph")
    assert h.is_heading() is True
    assert p.is_heading() is False
    assert h.is_structural_break() is True
    assert p.is_structural_break() is False