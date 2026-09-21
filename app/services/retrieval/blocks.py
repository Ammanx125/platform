# app/services/retrieval/blocks.py
"""
Blocks: the atomic structural units of a document.

A Block is the chunker's input unit. Content sources that understand their
own structure (PDF loader, markdown loader, etc.) emit blocks directly.
For raw text with no structure, split_into_paragraphs() is the fallback.

A Block's `kind` matters to the chunker:
  - "heading"     — start of a section; sticks to following content
  - "paragraph"   — a normal body of prose
  - "list_item"   — a bullet/entry; kept standalone if strategy is strict
  - "table_row"   — a row from a table; kept standalone if strategy is strict
  - "page_break"  — a marker; not emitted as content, but breaks continuity
  - "code"        — preformatted; not split on sentence boundaries
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Split on one or more blank lines. \s covers spaces/tabs/CR/LF.
_BLANK_LINE_RE = re.compile(r"\n\s*\n+")

# Sentence boundaries: after . ! ? followed by whitespace or end-of-string.
# Also handles common closing punctuation: quotes, parens, brackets.
# Not perfect (misses "Dr. Smith" style abbreviations), but good enough.
_SENTENCE_RE = re.compile(r"(?<=[.!?])[\"')\]]*\s+(?=[A-Z0-9\"'(])")

# Whitespace-collapse helper for normalizing block text.
_WS_RE = re.compile(r"[ \t]+")


@dataclass(frozen=True)
class Block:
    """
    One structural unit of a document.

    text:     the block's content. No trailing whitespace. May be empty
              (e.g. page_break markers), but empty blocks are dropped by
              the chunker.
    kind:     one of "heading" | "paragraph" | "list_item" | "table_row" |
              "page_break" | "code"
    metadata: kind-specific extras. Examples:
              - heading:    {"level": 1, "number": "3.2"}
              - paragraph:  {"page": 4}
              - list_item:  {"index": 3, "page": 4}
              - table_row:  {"row_index": 17, "page": 4}
    """
    text: str
    kind: str = "paragraph"
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_heading(self) -> bool:
        return self.kind == "heading"

    def is_structural_break(self) -> bool:
        """
        Blocks that should never merge with neighbors under strict strategy.
        Headings and page breaks are structural; prose and lists aren't.
        """
        return self.kind in ("heading", "page_break")


def _normalize_line_whitespace(text: str) -> str:
    # Collapse runs of spaces/tabs within a line, but keep newlines.
    return "\n".join(_WS_RE.sub(" ", line).strip() for line in text.splitlines())


def split_into_paragraphs(text: str) -> list[Block]:
    """
    Fallback block splitter for unstructured text.

    Splits on blank lines. Each resulting chunk of text becomes a paragraph
    Block. Empty results are dropped. Leading/trailing whitespace stripped.

    This is used when a caller passes raw_text with no structural hints.
    It is not a substitute for a real loader (PDF, markdown) that knows the
    document's actual structure.
    """
    if not text or not text.strip():
        return []

    raw_paras = _BLANK_LINE_RE.split(text)
    blocks: list[Block] = []
    for raw in raw_paras:
        normalized = _normalize_line_whitespace(raw).strip()
        if not normalized:
            continue
        blocks.append(Block(text=normalized, kind="paragraph"))
    return blocks


def split_into_sentences(text: str) -> list[str]:
    """
    Split text into sentences on terminal punctuation.

    Approximate — does not handle all cases (abbreviations like "Dr.",
    ellipses, decimal numbers). Good enough for the chunker's fallback when
    a single block exceeds the chunk target.

    Always returns at least one element for non-empty input.
    """
    t = text.strip()
    if not t:
        return []
    parts = _SENTENCE_RE.split(t)
    # Collapse any empty pieces and strip.
    out = [p.strip() for p in parts if p and p.strip()]
    return out if out else [t]