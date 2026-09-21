# app/services/retrieval/chunking.py
"""
Two chunkers:

  - chunk_text   — fixed-window token chunker (Step 7a). Retained for
                   reference and used by tests. Not used by the document
                   pipeline anymore; the block-aware chunker is.

  - chunk_blocks — structure-aware chunker (Step 7b.2). Respects paragraph,
                   heading, list-item, and table-row boundaries. Default
                   for all documents.

Design contract: chunk_blocks never splits a block unless the block itself
exceeds the target size. Oversized blocks are split on sentence boundaries
with a token-window fallback for pathological input.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.services.retrieval.blocks import Block, split_into_sentences

# ---------- shared output type ----------

@dataclass(frozen=True)
class TextChunk:
    ordinal: int
    text: str
    token_count: int
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------- helpers ----------

def _tokens(text: str) -> list[str]:
    return text.split()


def _token_count(text: str) -> int:
    return len(_tokens(text))


# ---------- fixed-window chunker (unchanged, kept for reference) ----------

def chunk_text(
    text: str,
    *,
    target_tokens: int | None = None,
    overlap_tokens: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> list[TextChunk]:
    """
    Split text into overlapping windows of approximately `target_tokens`
    whitespace tokens. Pre-block-aware behavior, retained for tests and as
    a fallback primitive.
    """
    target = target_tokens or settings.chunk_target_tokens
    overlap = overlap_tokens if overlap_tokens is not None else settings.chunk_overlap_tokens

    if target <= 0:
        raise ValueError("target_tokens must be positive")
    if overlap < 0 or overlap >= target:
        raise ValueError("overlap_tokens must be >= 0 and < target_tokens")

    tokens = _tokens(text)
    if not tokens:
        return []

    base_meta = dict(metadata or {})
    chunks: list[TextChunk] = []
    step = target - overlap
    ordinal = 0
    i = 0
    while i < len(tokens):
        window = tokens[i : i + target]
        chunk_str = " ".join(window)
        chunks.append(TextChunk(
            ordinal=ordinal,
            text=chunk_str,
            token_count=len(window),
            metadata={**base_meta, "start_token": i, "end_token": i + len(window)},
        ))
        ordinal += 1
        if i + target >= len(tokens):
            break
        i += step

    return chunks


# ---------- block-aware chunker ----------

def _split_oversized_block(
    block: Block,
    *,
    target_tokens: int,
) -> list[Block]:
    """
    Split a single block that exceeds target_tokens.

    Prefers sentence boundaries. Falls back to a hard token window for any
    "sentence" that is itself too long (flattened tables, minified JSON).

    Returned blocks preserve kind and metadata; each gets a
    "split_index" and "split_total" in metadata for traceability.
    """
    if _token_count(block.text) <= target_tokens:
        return [block]

    sentences = split_into_sentences(block.text)
    out: list[Block] = []
    current: list[str] = []
    current_tokens = 0

    def flush():
        nonlocal current, current_tokens
        if not current:
            return
        text = " ".join(current).strip()
        if not text:
            current = []
            current_tokens = 0
            return
        out.append(Block(
            text=text,
            kind=block.kind,
            metadata=dict(block.metadata),
        ))
        current = []
        current_tokens = 0

    for sentence in sentences:
        s_tokens = _token_count(sentence)
        if s_tokens > target_tokens:
            # Pathological: a single "sentence" exceeds target. Hard-split it.
            flush()
            toks = _tokens(sentence)
            for i in range(0, len(toks), target_tokens):
                window = " ".join(toks[i : i + target_tokens]).strip()
                if window:
                    out.append(Block(
                        text=window,
                        kind=block.kind,
                        metadata=dict(block.metadata),
                    ))
            continue
        if current_tokens + s_tokens > target_tokens:
            flush()
        current.append(sentence)
        current_tokens += s_tokens

    flush()

    for i, b in enumerate(out):
        b.metadata["split_index"] = i
        b.metadata["split_total"] = len(out)

    return out


def _render_chunk_text(prefix: str, body: str) -> str:
    """
    Combine heading prefix and block body into a chunk's text.

    Empty prefix -> body only. Non-empty prefix -> "prefix: body".
    Uses ': ' as the separator so retrieval results read naturally:
        "Payment Terms: The supplier shall pay..."
    """
    if not prefix:
        return body
    return f"{prefix}: {body}"


def chunk_blocks(
    blocks: list[Block],
    *,
    strategy: str | None = None,
    target_tokens: int | None = None,
    overlap_tokens: int | None = None,
    heading_prefix: bool | None = None,
    base_metadata: dict[str, Any] | None = None,
) -> list[TextChunk]:
    """
    Structure-aware chunker.

    strategy:
      - "continuity" (default): when a chunk fills up, the next chunk starts
        with the last block of the previous one, IF that block is no larger
        than `overlap_tokens * 3`. Preserves cross-boundary context without
        duplicating huge paragraphs.
      - "strict": no overlap. Each block goes into exactly one chunk. Chunks
        break at block boundaries.

    Headings:
      - A heading block is never separated from its following non-heading
        block (both strategies).
      - When `heading_prefix` is True, each chunk's text is prefixed with
        the current heading path (e.g. "Payment Terms > Penalties: ...").

    Oversized blocks:
      - Split on sentence boundaries via _split_oversized_block before
        chunk assembly. The resulting fragments participate normally.

    Empty blocks are dropped. `page_break` blocks are dropped but reset
    continuity (no overlap across a page break).
    """
    strategy = strategy or settings.chunk_strategy
    target = target_tokens or settings.chunk_target_tokens
    overlap = overlap_tokens if overlap_tokens is not None else settings.chunk_overlap_tokens
    use_heading_prefix = heading_prefix if heading_prefix is not None else settings.chunk_heading_prefix

    if strategy not in ("continuity", "strict"):
        raise ValueError(f"unknown strategy: {strategy!r}")
    if target <= 0:
        raise ValueError("target_tokens must be positive")
    if overlap < 0 or overlap >= target:
        raise ValueError("overlap_tokens must be >= 0 and < target_tokens")

    base_meta = dict(base_metadata or {})

    # Pre-pass: drop empties, split oversized blocks.
    prepared: list[Block] = []
    for b in blocks:
        if b.kind == "page_break":
            # Represent a page break as a marker block so continuity resets.
            prepared.append(b)
            continue
        if not b.text or not b.text.strip():
            continue
        prepared.extend(_split_oversized_block(b, target_tokens=target))

    chunks: list[TextChunk] = []

    # State carried across the assembly loop.
    pending_blocks: list[Block] = []       # blocks accumulated for current chunk
    pending_tokens = 0
    heading_stack: list[tuple[int, str]] = []   # (level, text)
    last_emitted_block: Block | None = None
    last_kinds: list[str] = []

    def current_prefix() -> str:
        if not use_heading_prefix or not heading_stack:
            return ""
        return " > ".join(h[1] for h in heading_stack)

    def emit():
        nonlocal pending_blocks, pending_tokens, last_emitted_block, last_kinds
        if not pending_blocks:
            return
        body = "\n\n".join(b.text for b in pending_blocks)
        prefix = current_prefix()
        text = _render_chunk_text(prefix, body)
        kinds = [b.kind for b in pending_blocks]
        chunks.append(TextChunk(
            ordinal=len(chunks),
            text=text,
            token_count=_token_count(text),
            metadata={
                **base_meta,
                "headings": [h[1] for h in heading_stack],
                "block_kinds": kinds,
                "strategy": strategy,
            },
        ))
        last_emitted_block = pending_blocks[-1]
        last_kinds = kinds
        pending_blocks = []
        pending_tokens = 0

    def start_continuity_from(block: Block) -> None:
        """
        Begin the next chunk with a repeated copy of `block` (used by the
        continuity strategy). No-op if the block is too large to duplicate
        or if the strategy is strict.
        """
        nonlocal pending_blocks, pending_tokens
        if strategy != "continuity":
            return
        if _token_count(block.text) > overlap * 3:
            return
        pending_blocks = [block]
        pending_tokens = _token_count(block.text)

    for block in prepared:
        if block.kind == "page_break":
            # Emit what we have, reset continuity, do not carry a heading.
            emit()
            last_emitted_block = None
            # Heading stack persists across pages within a section — that's
            # intentional. A section heading doesn't stop mattering because
            # a page ended. Clear only if we want per-page semantics, which
            # we don't.
            continue

        if block.is_heading():
            # A heading starts a new section only after any body content
            # already pending. Consecutive headings stay together so nested
            # headings can form one prefix for the following body.
            if pending_blocks and any(not b.is_heading() for b in pending_blocks):
                emit()
            elif pending_blocks:
                pending_blocks = []
                pending_tokens = 0
            level = int(block.metadata.get("level", 1))
            # Pop deeper-or-equal levels to build the current path.
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, block.text))
            # Add the heading itself as the first block of the next chunk.
            pending_blocks.append(block)
            pending_tokens += _token_count(block.text)
            continue

        # Non-heading block. Does it fit?
        b_tokens = _token_count(block.text)
        if pending_tokens + b_tokens > target and pending_blocks:
            emit()
            # Continuity: start next chunk with the last emitted block.
            if last_emitted_block is not None:
                start_continuity_from(last_emitted_block)

        pending_blocks.append(block)
        pending_tokens += b_tokens

    emit()

    return chunks