# app/services/knowledge/service.py
"""
Document lifecycle: create from text, chunk, embed, delete, re-chunk.

Not a retriever. Lives alongside app/services/retrieval/ but concerns
itself with the *state* of Documents and Chunks, not with querying them.
"""
from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.knowledge import Chunk, Document
from app.services.retrieval.chunking import chunk_text
from app.services.retrieval.embeddings.registry import get_embedding_provider


async def create_document(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    title: str,
    content_type: str,
    raw_text: str,
    source_id: uuid.UUID | None = None,
    doc_metadata: dict | None = None,
) -> Document:
    """
    Create a Document and immediately chunk + embed it.

    Synchronous within the request for Step 7a. If chunking becomes slow
    (large PDFs, many documents), move it to the worker like ingestion.
    """
    doc = Document(
        tenant_id=tenant_id,
        source_id=source_id,
        title=title,
        content_type=content_type,
        raw_text=raw_text,
        doc_metadata=doc_metadata or {},
        status="processing",
    )
    db.add(doc)
    await db.flush()

    try:
        await _chunk_and_embed(db, document=doc)
        doc.status = "ready"
        doc.error_message = None
    except Exception as exc:  # noqa: BLE001
        doc.status = "failed"
        doc.error_message = str(exc)[:2000]

    await db.flush()
    return doc


async def _chunk_and_embed(db: AsyncSession, *, document: Document) -> None:
    """
    Chunk the document's raw_text and insert Chunk rows with embeddings.

    Replaces any existing chunks (used by both create and re-chunk).
    """
    # Clear existing chunks first (supports re-chunk)
    await db.execute(delete(Chunk).where(Chunk.document_id == document.id))

    pieces = chunk_text(
        document.raw_text,
        metadata={"document_id": str(document.id)},
    )

    if not pieces:
        document.chunk_count = 0
        document.token_count = 0
        return

    provider = get_embedding_provider()
    texts = [p.text for p in pieces]
    vectors = await provider.embed(texts=texts)

    total_tokens = 0
    for piece, vec in zip(pieces, vectors, strict=True):
        db.add(Chunk(
            document_id=document.id,
            ordinal=piece.ordinal,
            text=piece.text,
            token_count=piece.token_count,
            embedding=vec,
            chunk_metadata=piece.metadata,
        ))
        total_tokens += piece.token_count

    document.chunk_count = len(pieces)
    document.token_count = total_tokens
    await db.flush()


async def rechunk_document(db: AsyncSession, *, document_id: uuid.UUID) -> Document:
    """Re-chunk a document. Useful after changing chunking strategy."""
    doc = (
        await db.execute(select(Document).where(Document.id == document_id))
    ).scalar_one_or_none()
    if doc is None:
        raise ValueError(f"document not found: {document_id}")

    doc.status = "processing"
    await db.flush()
    try:
        await _chunk_and_embed(db, document=doc)
        doc.status = "ready"
        doc.error_message = None
    except Exception as exc:  # noqa: BLE001
        doc.status = "failed"
        doc.error_message = str(exc)[:2000]
    await db.flush()
    return doc


async def delete_document(db: AsyncSession, *, document_id: uuid.UUID) -> None:
    """
    Explicit delete. Chunks are removed by the FK ON DELETE CASCADE, but we
    delete them here too so the operation is one transaction and observable
    in the audit trail later.
    """
    await db.execute(delete(Chunk).where(Chunk.document_id == document_id))
    await db.execute(delete(Document).where(Document.id == document_id))
    await db.flush()