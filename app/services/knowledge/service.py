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
from app.services.retrieval.blocks import Block, split_into_paragraphs
from app.services.retrieval.chunking import chunk_blocks
from app.services.retrieval.embeddings.registry import get_embedding_provider


async def create_document(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    title: str,
    content_type: str,
    raw_text: str,
    blocks: list[Block] | None = None,
    source_id: uuid.UUID | None = None,
    doc_metadata: dict | None = None,
) -> Document:
    """
    Create a Document and immediately chunk + embed it.

    `blocks` is optional. When provided (e.g. by a PDF loader that knows the
    document's structure), the chunker respects those blocks. When omitted,
    raw_text is split into paragraph blocks with a blank-line heuristic.

    Synchronous within the request for now. If chunking becomes slow (large
    PDFs, many documents), move it to the worker like ingestion.
    """
    merged_meta = dict(doc_metadata or {})
    # Stash blocks in metadata so rechunk_document can re-use them without
    # needing the caller to re-supply them. Kept under a private key so it
    # doesn't collide with user metadata.
    if blocks is not None:
        merged_meta["_blocks"] = [
            {"text": b.text, "kind": b.kind, "metadata": b.metadata}
            for b in blocks
        ]

    doc = Document(
        tenant_id=tenant_id,
        source_id=source_id,
        title=title,
        content_type=content_type,
        raw_text=raw_text,
        doc_metadata=merged_meta,
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


def _blocks_from_document(document: Document) -> list[Block]:
    """
    Reconstruct blocks for a document. If the document carries explicit
    blocks (from a loader), use those. Otherwise synthesize paragraphs
    from raw_text.
    """
    stored = document.doc_metadata.get("_blocks") if document.doc_metadata else None
    if stored:
        return [
            Block(
                text=b["text"],
                kind=b.get("kind", "paragraph"),
                metadata=dict(b.get("metadata", {})),
            )
            for b in stored
        ]
    return split_into_paragraphs(document.raw_text)


async def _chunk_and_embed(db: AsyncSession, *, document: Document) -> None:
    """
    Chunk the document and insert Chunk rows with embeddings.

    Replaces any existing chunks (used by both create and re-chunk).
    """
    await db.execute(delete(Chunk).where(Chunk.document_id == document.id))

    blocks = _blocks_from_document(document)

    strategy = None
    if document.doc_metadata:
        strategy = document.doc_metadata.get("chunk_strategy")

    pieces = chunk_blocks(
        blocks,
        strategy=strategy,
        base_metadata={"document_id": str(document.id)},
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