# app/services/retrieval/base.py
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class RetrievalFilters:
    """
    Constraints applied to a retrieval. All fields optional.

    document_ids: restrict to specific documents.
    source_ids:   restrict to documents whose source_id is one of these.
    content_types: restrict by document content_type.
    """
    document_ids: list[uuid.UUID] = field(default_factory=list)
    source_ids: list[uuid.UUID] = field(default_factory=list)
    content_types: list[str] = field(default_factory=list)


@dataclass
class RetrievedItem:
    """
    One piece of evidence returned by a retriever.

    kind: "chunk" | "row"
    score_components: how the final score was composed. For a pure vector
        retriever this is {"vector": 0.87}. For hybrid it's
        {"vector": 0.87, "keyword": 0.42, "weighted": 0.65}.
    """
    kind: str
    id: uuid.UUID
    score: float
    content: str
    metadata: dict[str, Any]
    score_components: dict[str, float] = field(default_factory=dict)


@dataclass
class RetrievalResult:
    items: list[RetrievedItem]
    query: str
    strategy: str
    filters: dict[str, Any]


class Retriever(Protocol):
    """
    Contract for evidence collection.

    Implementations:
      - VectorRetriever    — pgvector cosine similarity over Chunk.embedding
      - KeywordRetriever   — Postgres FTS over Chunk.text
      - SQLRetriever       — structured query over StagedRow.raw_data
      - HybridRetriever    — composes the above, normalizes, merges

    A future implementation (Elasticsearch, Vespa) implements the same
    interface without the rest of Sansa caring.
    """
    name: str

    async def retrieve(
        self,
        *,
        db: Any,               # AsyncSession; typed Any to avoid circular import
        tenant_id: uuid.UUID,
        query: str,
        top_k: int,
        filters: RetrievalFilters | None = None,
    ) -> RetrievalResult: ...