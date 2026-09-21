# app/api/v1/knowledge.py
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentTenantId, require_permission
from app.db.models.knowledge import Chunk, Document
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.knowledge import (
    ChunkRead,
    DocumentCreate,
    DocumentRead,
    DocumentWithChunks,
    RetrievedItemRead,
    SearchRequest,
    SearchResponse,
)
from app.services.knowledge import service as knowledge_service
from app.services.retrieval.base import RetrievalFilters
from app.services.retrieval.hybrid import HybridRetriever
from app.services.retrieval.keyword import KeywordRetriever
from app.services.retrieval.sql import SQLRetriever
from app.services.retrieval.vector import VectorRetriever

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


# ---------- documents ----------

@router.post(
    "/documents",
    response_model=DocumentRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_document(
    body: DocumentCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("knowledge:write"))],
) -> Document:
    doc = await knowledge_service.create_document(
        db,
        tenant_id=tenant_id,
        title=body.title,
        content_type=body.content_type,
        raw_text=body.raw_text,
        source_id=body.source_id,
        doc_metadata=body.doc_metadata,
    )
    await db.commit()
    await db.refresh(doc)
    return doc


@router.get("/documents", response_model=list[DocumentRead])
async def list_documents(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("knowledge:read"))],
) -> list[Document]:
    stmt = (
        select(Document)
        .where(Document.tenant_id == tenant_id)
        .order_by(Document.created_at.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


@router.get("/documents/{document_id}", response_model=DocumentWithChunks)
async def get_document(
    document_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("knowledge:read"))],
) -> DocumentWithChunks:
    doc = (
        await db.execute(
            select(Document).where(
                Document.id == document_id,
                Document.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="document not found")

    chunks = (
        await db.execute(
            select(Chunk)
            .where(Chunk.document_id == document_id)
            .order_by(Chunk.ordinal)
        )
    ).scalars().all()

    return DocumentWithChunks.model_validate({
        **DocumentRead.model_validate(doc).model_dump(),
        "chunks": [chunk_read_from_model(c) for c in chunks],
    })


def chunk_read_from_model(chunk: Chunk) -> ChunkRead:
    return ChunkRead.model_validate(chunk)


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("knowledge:write"))],
) -> None:
    doc = (
        await db.execute(
            select(Document).where(
                Document.id == document_id,
                Document.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="document not found")

    await knowledge_service.delete_document(db, document_id=document_id)
    await db.commit()


@router.post(
    "/documents/{document_id}/rechunk",
    response_model=DocumentRead,
)
async def rechunk_document(
    document_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("knowledge:write"))],
) -> Document:
    doc = (
        await db.execute(
            select(Document).where(
                Document.id == document_id,
                Document.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="document not found")

    doc = await knowledge_service.rechunk_document(db, document_id=document_id)
    await db.commit()
    await db.refresh(doc)
    return doc


# ---------- search ----------

def _make_retriever(strategy: str):
    if strategy == "vector":
        return VectorRetriever()
    if strategy == "keyword":
        return KeywordRetriever()
    if strategy == "sql":
        return SQLRetriever()
    if strategy == "hybrid":
        return HybridRetriever()
    raise ValueError(f"unknown strategy: {strategy!r}")


@router.post("/search", response_model=SearchResponse)
async def search(
    body: SearchRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("knowledge:read"))],
) -> SearchResponse:
    retriever = _make_retriever(body.strategy)
    filters = RetrievalFilters(
        document_ids=body.filters.document_ids,
        source_ids=body.filters.source_ids,
        content_types=body.filters.content_types,
    )
    result = await retriever.retrieve(
        db=db,
        tenant_id=tenant_id,
        query=body.query,
        top_k=body.top_k,
        filters=filters,
    )
    return SearchResponse(
        query=result.query,
        strategy=result.strategy,
        items=[
            RetrievedItemRead(
                kind=it.kind,
                id=it.id,
                score=it.score,
                content=it.content,
                metadata=it.metadata,
                score_components=it.score_components,
            )
            for it in result.items
        ],
    )