# app/db/models/webhook.py
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class WebhookDelivery(Base, UUIDPrimaryKeyMixin, TimestampMixin, TenantMixin):
    """
    One inbound webhook payload.

    raw_body is the exact bytes received, before parsing. It's stored so
    that (a) signature verification is reproducible and (b) we can
    re-process a delivery if the parsing logic changes.

    status: 'pending' -> 'staged' | 'failed'
    """
    __tablename__ = "webhook_deliveries"

    source_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("data_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("ingestion_jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", index=True
    )

    headers: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    raw_body: Mapped[bytes] = mapped_column(nullable=False)