# app/db/models/timestamps.py
"""
Time-basis infrastructure.

Sansa does not assume one time model. A row can carry several meaningful
timestamps, and anomaly detection, retrieval, and analytics choose which
one to reason about:

  - content:   time the data itself describes (order_date, transaction_date)
  - ingestion: time the platform first saw the row (always present)
  - file:      time the source file was created/modified on disk
  - stream:    event time declared by a webhook/event payload

RowTimestamp stores one row per (staged_row, kind). Ingestion is written
automatically. Others are written by connectors that know them.

FileObservation remembers files without storing them:
  - the same file seen twice is one record with a bumped last_seen_at
  - content_hash lets us detect modifications without keeping bytes
  - tenant + source scoping; the storage_key is optional (may be GC'd)
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class RowTimestamp(Base, UUIDPrimaryKeyMixin, TimestampMixin, TenantMixin):
    """
    One timestamp attached to one staged row, tagged by kind.

    kind is a string, not a DB enum, to keep migrations cheap. The allowed
    values live in app/services/timestamps/constants.py (TimeBasis).
    """
    __tablename__ = "row_timestamps"

    staged_row_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("staged_rows.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    kind: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    # Free-form extra context: which raw column produced this timestamp,
    # what the file path was, etc.
    context: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "staged_row_id", "kind",
            name="uq_row_timestamps_row_kind",
        ),
        Index("ix_row_timestamps_tenant_kind_ts", "tenant_id", "kind", "timestamp"),
    )


class FileObservation(Base, UUIDPrimaryKeyMixin, TimestampMixin, TenantMixin):
    """
    A file the platform has seen, whether or not the bytes are stored.

    Populated by:
      - the upload endpoint (immediate, on POST /datasets/{id}/upload)
      - the on-prem agent (future, when it reports watched folders)

    Lifecycle:
      - first time a (tenant, source, path) is seen -> status="new"
      - subsequent observation with same content_hash -> last_seen_at bumped,
        status="unchanged"
      - subsequent observation with different content_hash -> status="changed",
        and a new row is inserted (the old one is not modified, so history
        is preserved)
      - agent reports deletion -> status="deleted" on the latest row

    The uniqueness constraint is (tenant_id, source_id, path, content_hash):
    the same file *content* at the same path is one record. A modified file
    produces a new record with the new hash, linked in time by path.
    """
    __tablename__ = "file_observations"

    source_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("data_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    path: Mapped[str] = mapped_column(String(1000), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Filesystem timestamps reported by the source. Nullable because the
    # upload endpoint (browser) can't reliably provide them; the agent can.
    file_mtime: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    file_ctime: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # 'new' | 'changed' | 'unchanged' | 'deleted'
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="new", index=True
    )

    # Where the bytes live, if we stored them. May be null (agent-only
    # sources where we never store a copy, or GC'd after processing).
    storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "source_id", "path", "content_hash",
            name="uq_file_observations_identity",
        ),
        Index("ix_file_observations_tenant_path", "tenant_id", "path"),
        Index("ix_file_observations_source_status", "source_id", "status"),
    )