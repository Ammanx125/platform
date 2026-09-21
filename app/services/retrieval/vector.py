# app/services/retrieval/vector.py
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.knowledge import Chunk, Document
from app.services.retrieval.base import (
    RetrievalFilters,
    RetrievalResult,
    RetrievedItem,
)
from app.services.retrieval.embeddings.registry import get_embedding_provider


class VectorRetriever:
    name = "vector"

    async def retrieve(
        self,
        *,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        query: str,
        top_k: int,
        filters: RetrievalFilters | None = None,
    ) -> RetrievalResult:
        provider = get_embedding_provider()
        query_vec = await provider.embed_one(text=query)

        # Build the base query, always filtered by tenant through the
        # parent Document.
        stmt = (
            select(
                Chunk,
                Document.id.label("doc_id"),
                Document.title.label("doc_title"),
                Document.source_id.label("doc_source_id"),
                Document.content_type.label("doc_content_type"),
                Chunk.embedding.cosine_distance(query_vec).label("distance"),
            )
            .join(Document, Document.id == Chunk.document_id)
            .where(Document.tenant_id == tenant_id)
        )

        f = filters or RetrievalFilters()
        if f.document_ids:
            stmt = stmt.where(Document.id.in_(f.document_ids))
        if f.source_ids:
            stmt = stmt.where(Document.source_id.in_(f.source_ids))
        if f.content_types:
            stmt = stmt.where(Document.content_type.in_(f.content_types))

        stmt = stmt.order_by(Chunk.embedding.cosine_distance(query_vec)).limit(top_k)

        rows = (await db.execute(stmt)).all()

        items: list[RetrievedItem] = []
        for chunk, doc_id, doc_title, doc_source_id, doc_content_type, distance in rows:
            # cosine_distance in [0, 2]; similarity = 1 - distance (in [-1, 1]).
            # Clamp to [0, 1] so downstream merging has a predictable range.
            similarity = max(0.0, 1.0 - float(distance))
            items.append(RetrievedItem(
                kind="chunk",
                id=chunk.id,
                score=similarity,
                content=chunk.text,
                metadata={
                    "document_id": str(doc_id),
                    "document_title": doc_title,
                    "source_id": str(doc_source_id) if doc_source_id else None,
                    "content_type": doc_content_type,
                    "ordinal": chunk.ordinal,
                },
                score_components={"vector": similarity},
            ))

        return RetrievalResult(
            items=items,
            query=query,
            strategy=self.name,
            filters={
                "document_ids": [str(i) for i in f.document_ids],
                "source_ids": [str(i) for i in f.source_ids],
                "content_types": f.content_types,
            },
        )