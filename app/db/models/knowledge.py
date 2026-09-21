# app/db/models/knowledge.py
from __future__ import annotations

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class Document(Base, UUIDPrimaryKeyMixin, TimestampMixin, TenantMixin):
    """
    A logical unstructured knowledge item.

    A Document is Sansa's internal representation of "one coherent piece of
    knowledge" — NOT necessarily the physical file. A 40-page PDF is one
    Document; a single API record can be one Document; a pasted text note is
    one Document.

    Source lineage:
      - source_id is nullable. Manual documents (pasted text, notes, FAQ
        entries) have no upstream DataSource.
      - When source_id is set, the Document came from an ingestion.

    status:
      - 'pending'    — created, not yet chunked
      - 'processing' — chunking / embedding in progress
      - 'ready'      — chunks exist and are embedded
      - 'failed'     — something broke; see error_message
    """
    __tablename__ = "documents"

    source_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("data_sources.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    title: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(40), nullable=False)
    # content_type: "text" | "markdown" | "pdf" | "html" | "json" | ...

    raw_text: Mapped[str] = mapped_column(Text, nullable=False)

    # Arbitrary document-level metadata (original filename, page count,
    # ingestion cursor, API endpoint, etc.)
    doc_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", index=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    chunks = relationship(
        "Chunk",
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="Chunk.ordinal",
        passive_deletes=True,
    )


class Chunk(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    A retrievable passage of a Document.

    Chunks are the unit that gets:
      - indexed by vector
      - indexed by full-text search
      - returned as evidence by retrievers

    No tenant_id column. Tenant is inherited through the parent Document.
    Every retrieval query joins through documents to filter by tenant.

    embedding dimension comes from settings.embedding_dimensions. Changing
    this value requires a migration (ALTER COLUMN TYPE vector(N)).
    """
    __tablename__ = "chunks"

    document_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    embedding: Mapped[list[float]] = mapped_column(
        Vector(settings.embedding_dimensions), nullable=False
    )

    # Chunk-level metadata (page number, section heading, row_number, etc.)
    chunk_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    document = relationship("Document", back_populates="chunks")

    __table_args__ = (
        UniqueConstraint("document_id", "ordinal", name="uq_chunks_doc_ordinal"),
    )