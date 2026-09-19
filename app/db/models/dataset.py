# app/db/models/dataset.py
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class DataSource(Base, UUIDPrimaryKeyMixin, TimestampMixin, TenantMixin):
    """
    A registered data source belonging to one tenant.

    source_type is one of: 'csv', 'excel' (Step 4a);
    'sql', 'http', 'webhook' (Step 4b).

    config holds connector-specific settings (SQL URL ref, HTTP endpoint,
    auth reference id, etc). Never store raw credentials here — store a
    reference (e.g. a secrets-manager key) instead.
    """
    __tablename__ = "data_sources"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    jobs = relationship(
        "IngestionJob",
        back_populates="source",
        cascade="all, delete-orphan",
        order_by="IngestionJob.created_at.desc()",
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_data_sources_tenant_name"),
    )


class IngestionJob(Base, UUIDPrimaryKeyMixin, TimestampMixin, TenantMixin):
    """
    One execution of an ingestion on one DataSource.

    status: 'pending' -> 'running' -> 'succeeded' | 'failed'
    """
    __tablename__ = "ingestion_jobs"

    source_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("data_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", index=True
    )

    # Worker bookkeeping
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Counts (denormalized for fast UI reads; lineage table has details)
    rows_read: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_staged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    source = relationship("DataSource", back_populates="jobs")
    lineage = relationship(
        "IngestionLineage",
        back_populates="job",
        cascade="all, delete-orphan",
        uselist=False,
    )


class IngestionLineage(Base, UUIDPrimaryKeyMixin, TimestampMixin, TenantMixin):
    """
    Evidence of what was read during one ingestion job.
    One-to-one with IngestionJob.

    Later (Step 5) field-level lineage (source_col -> canonical concept)
    lands in SemanticMapping, not here. This table is file/source provenance.
    """
    __tablename__ = "ingestion_lineage"

    job_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("ingestion_jobs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # File-based sources
    storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    original_filename: Mapped[str | None] = mapped_column(String(500), nullable=True)
    file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    byte_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Non-file sources (4b)
    source_uri: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    source_cursor: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Parse metadata
    sheet_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    encoding: Mapped[str | None] = mapped_column(String(40), nullable=True)
    delimiter: Mapped[str | None] = mapped_column(String(4), nullable=True)
    header_row: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Errors surfaced during parsing (row-level, non-fatal)
    errors: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    job = relationship("IngestionJob", back_populates="lineage")


class StagedRow(Base, UUIDPrimaryKeyMixin, TimestampMixin, TenantMixin):
    """
    A raw parsed row waiting for semantic mapping (Step 5).

    raw_data holds the column-name -> value mapping as parsed. It is
    intentionally schemaless; different customers have different columns
    until Step 5 maps them to canonical concepts.
    """
    __tablename__ = "staged_rows"

    job_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("ingestion_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("data_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        UniqueConstraint("job_id", "row_number", name="uq_staged_rows_job_row"),
    )