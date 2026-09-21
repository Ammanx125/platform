# app/services/retrieval/keyword.py
"""
Keyword retriever using Postgres full-text search.

This is "BM25-style" — tf-idf weighting of lexemes via ts_rank_cd — not the
literal BM25 algorithm. For the tier Sansa is at, this satisfies the
blueprint's requirement. Swapping to real BM25 (pg_search extension, or an
external search engine) is a matter of writing a new Retriever that
implements the same interface.
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.knowledge import Chunk, Document
from app.services.retrieval.base import (
    RetrievalFilters,
    RetrievalResult,
    RetrievedItem,
)


class KeywordRetriever:
    name = "keyword"

    async def retrieve(
        self,
        *,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        query: str,
        top_k: int,
        filters: RetrievalFilters | None = None,
    ) -> RetrievalResult:
        # Use 'english' config for stemming. Swap for tenant-configured
        # language later if needed.
        tsquery = func.plainto_tsquery("english", query)
        tsvector = func.to_tsvector("english", Chunk.text)
        rank = func.ts_rank_cd(tsvector, tsquery).label("rank")

        stmt = (
            select(
                Chunk,
                Document.id.label("doc_id"),
                Document.title.label("doc_title"),
                Document.source_id.label("doc_source_id"),
                Document.content_type.label("doc_content_type"),
                rank,
            )
            .join(Document, Document.id == Chunk.document_id)
            .where(Document.tenant_id == tenant_id)
            .where(tsvector.op("@@")(tsquery))
        )

        f = filters or RetrievalFilters()
        if f.document_ids:
            stmt = stmt.where(Document.id.in_(f.document_ids))
        if f.source_ids:
            stmt = stmt.where(Document.source_id.in_(f.source_ids))
        if f.content_types:
            stmt = stmt.where(Document.content_type.in_(f.content_types))

        stmt = stmt.order_by(rank.desc()).limit(top_k)

        rows = (await db.execute(stmt)).all()

        items: list[RetrievedItem] = []
        for chunk, doc_id, doc_title, doc_source_id, doc_content_type, r in rows:
            score = float(r)  # ts_rank_cd returns float; not normalized
            items.append(RetrievedItem(
                kind="chunk",
                id=chunk.id,
                score=score,
                content=chunk.text,
                metadata={
                    "document_id": str(doc_id),
                    "document_title": doc_title,
                    "source_id": str(doc_source_id) if doc_source_id else None,
                    "content_type": doc_content_type,
                    "ordinal": chunk.ordinal,
                },
                score_components={"keyword": score},
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