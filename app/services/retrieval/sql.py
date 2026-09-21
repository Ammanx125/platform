# app/services/retrieval/sql.py
"""
Structured retrieval over StagedRow.raw_data.

Does NOT generate SQL from natural language. That's a Step 10 orchestrator
concern (and needs the LLM + a read-only SQL policy gate). This retriever
does simple, safe, deterministic matching: give it a text query, it scores
staged rows by whether their stringified values contain the query tokens.

The orchestrator will use this to answer "which rows mention supplier X" or
"find the row where SKU = ACME-42". More sophisticated structured queries
run through the analytics service (Step 8), not here.
"""
from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.dataset import DataSource, StagedRow
from app.services.retrieval.base import (
    RetrievalFilters,
    RetrievalResult,
    RetrievedItem,
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


class SQLRetriever:
    name = "sql"

    async def retrieve(
        self,
        *,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        query: str,
        top_k: int,
        filters: RetrievalFilters | None = None,
    ) -> RetrievalResult:
        query_tokens = _tokens(query)
        if not query_tokens:
            return RetrievalResult(
                items=[],
                query=query,
                strategy=self.name,
                filters={},
            )

        stmt = (
            select(StagedRow, DataSource.name.label("source_name"))
            .join(DataSource, DataSource.id == StagedRow.source_id)
            .where(StagedRow.tenant_id == tenant_id)
        )

        f = filters or RetrievalFilters()
        if f.source_ids:
            stmt = stmt.where(StagedRow.source_id.in_(f.source_ids))

        # Pull a bounded candidate set. Full-table scan on StagedRow is fine
        # for Step 7a where test data is small. A production version would
        # use a GIN index on raw_data or push down to a materialized
        # canonical table (Step 6+). See NOTE below.
        stmt = stmt.limit(2000)

        rows = (await db.execute(stmt)).all()

        scored: list[RetrievedItem] = []
        for row, source_name in rows:
            row_text = " ".join(str(v) for v in row.raw_data.values())
            row_tokens = _tokens(row_text)
            if not row_tokens:
                continue
            overlap = query_tokens & row_tokens
            if not overlap:
                continue
            # Jaccard-ish score: |overlap| / |query_tokens|
            score = len(overlap) / len(query_tokens)
            scored.append(RetrievedItem(
                kind="row",
                id=row.id,
                score=score,
                content=row_text,
                metadata={
                    "source_id": str(row.source_id),
                    "source_name": source_name,
                    "row_number": row.row_number,
                    "raw_data": row.raw_data,
                },
                score_components={"sql": score},
            ))

        scored.sort(key=lambda x: x.score, reverse=True)
        scored = scored[:top_k]

        return RetrievalResult(
            items=scored,
            query=query,
            strategy=self.name,
            filters={
                "source_ids": [str(i) for i in f.source_ids],
                "content_types": f.content_types,
            },
        )