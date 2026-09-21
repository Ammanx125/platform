# app/services/retrieval/hybrid.py
"""
Hybrid retriever: runs multiple strategies, normalizes, merges.

Score normalization is the whole game here. Vector scores are already
roughly [0, 1]. Keyword scores from ts_rank_cd are [0, 0.5] typically. SQL
scores are Jaccard [0, 1]. You can't just add them.

Strategy: for each sub-retriever's result set, min-max normalize to [0, 1]
within that result set, then take the weighted sum per unique item.

Ties are broken by the vector score (which is the closest thing we have to
a semantic signal).
"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.retrieval.base import (
    RetrievalFilters,
    RetrievalResult,
    RetrievedItem,
    Retriever,
)
from app.services.retrieval.keyword import KeywordRetriever
from app.services.retrieval.sql import SQLRetriever
from app.services.retrieval.vector import VectorRetriever


def _normalize(items: list[RetrievedItem]) -> dict[uuid.UUID, float]:
    """
    Min-max normalize scores in [0, 1]. Returns {item_id: normalized_score}.

    If all scores are identical (or there's only one), every item gets 1.0.
    That's a choice: a single result with the top score is "the best we have."
    """
    if not items:
        return {}
    scores = [it.score for it in items]
    lo, hi = min(scores), max(scores)
    if hi == lo:
        return {it.id: 1.0 for it in items}
    span = hi - lo
    return {it.id: (it.score - lo) / span for it in items}


class HybridRetriever:
    name = "hybrid"

    def __init__(
        self,
        *,
        vector: Retriever | None = None,
        keyword: Retriever | None = None,
        sql: Retriever | None = None,
    ) -> None:
        self.vector = vector or VectorRetriever()
        self.keyword = keyword or KeywordRetriever()
        self.sql = sql or SQLRetriever()

    async def retrieve(
        self,
        *,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        query: str,
        top_k: int,
        filters: RetrievalFilters | None = None,
    ) -> RetrievalResult:
        # Fetch more candidates than top_k from each retriever so merging
        # has room. A common heuristic: 3x top_k per source.
        candidate_k = max(top_k * 3, 20)

        vec_res = await self.vector.retrieve(
            db=db, tenant_id=tenant_id, query=query,
            top_k=candidate_k, filters=filters,
        )
        kw_res = await self.keyword.retrieve(
            db=db, tenant_id=tenant_id, query=query,
            top_k=candidate_k, filters=filters,
        )
        sql_res = await self.sql.retrieve(
            db=db, tenant_id=tenant_id, query=query,
            top_k=candidate_k, filters=filters,
        )

        vec_norm = _normalize(vec_res.items)
        kw_norm = _normalize(kw_res.items)
        sql_norm = _normalize(sql_res.items)

        # Index by item id so we can merge across sources.
        by_id: dict[uuid.UUID, RetrievedItem] = {}
        for it in vec_res.items + kw_res.items + sql_res.items:
            by_id.setdefault(it.id, it)

        w_vec = settings.retrieval_vector_weight
        w_kw = settings.retrieval_keyword_weight
        w_sql = settings.retrieval_sql_weight

        merged: list[RetrievedItem] = []
        for item_id, base in by_id.items():
            v = vec_norm.get(item_id, 0.0)
            k = kw_norm.get(item_id, 0.0)
            s = sql_norm.get(item_id, 0.0)
            weighted = w_vec * v + w_kw * k + w_sql * s
            merged.append(RetrievedItem(
                kind=base.kind,
                id=base.id,
                score=weighted,
                content=base.content,
                metadata=base.metadata,
                score_components={
                    "vector": round(v, 4),
                    "keyword": round(k, 4),
                    "sql": round(s, 4),
                    "weighted": round(weighted, 4),
                },
            ))

        # Sort by weighted score, tie-break by vector contribution.
        merged.sort(
            key=lambda x: (x.score, x.score_components.get("vector", 0.0)),
            reverse=True,
        )
        merged = merged[:top_k]

        return RetrievalResult(
            items=merged,
            query=query,
            strategy=self.name,
            filters={
                "document_ids": [str(i) for i in (filters or RetrievalFilters()).document_ids],
                "source_ids": [str(i) for i in (filters or RetrievalFilters()).source_ids],
                "content_types": (filters or RetrievalFilters()).content_types,
            },
        )