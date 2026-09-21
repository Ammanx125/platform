# app/services/retrieval/chunking.py
"""
Simple token-aware chunker.

Windows of `chunk_target_tokens` tokens with `chunk_overlap_tokens` overlap.
Not semantic chunking — that's a Step 7b improvement (split on paragraph /
section boundaries before falling back to fixed windows).

Token approximation: split on whitespace. This is not the same as the
embedding model's tokenizer, but for chunk sizing it's close enough and
avoids a tokenizer dependency in Step 7a. When a real embedding provider
lands, revisit: it may have a preferred tokenizer we should respect.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings


@dataclass(frozen=True)
class TextChunk:
    ordinal: int
    text: str
    token_count: int
    metadata: dict


def _approx_tokens(text: str) -> list[str]:
    return text.split()


def chunk_text(
    text: str,
    *,
    target_tokens: int | None = None,
    overlap_tokens: int | None = None,
    metadata: dict | None = None,
) -> list[TextChunk]:
    """
    Split text into overlapping windows of approximately `target_tokens`
    whitespace tokens.

    Returns an empty list for empty text. Otherwise returns at least one
    chunk covering the entire input.
    """
    target = target_tokens or settings.chunk_target_tokens
    overlap = overlap_tokens if overlap_tokens is not None else settings.chunk_overlap_tokens

    if target <= 0:
        raise ValueError("target_tokens must be positive")
    if overlap < 0 or overlap >= target:
        raise ValueError("overlap_tokens must be >= 0 and < target_tokens")

    tokens = _approx_tokens(text)
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