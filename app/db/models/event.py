# app/db/models/event.py
"""
Generic event log.

This is a *supplement* to the specialized event-carrying tables
(WebhookDelivery, FileObservation, IngestionJob, etc.), not a replacement.
Those tables carry channel-specific fields. `Event` is the generic,
filterable index: "at time T, from source S, event E occurred for tenant X."

The event store is what workflow triggers filter against, and it's what
the audit trail (Step 19) reads for "what happened recently in this
tenant?"

Event types currently emitted (Step 17 v1):
  - webhook.received
  - file.observed
  - ingestion.completed
  - ingestion.failed
  - detector.fired
  - forecast.completed

Future: action.executed, action.failed, workflow.completed,
workflow.failed, schedule.tick.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class Event(Base, UUIDPrimaryKeyMixin, TimestampMixin, TenantMixin):
    """
    One thing that happened, in the platform's view.

    dedup_key: optional. When set, uniqueness is enforced per tenant. Used
    by producers that may deliver the same event twice (webhook retries,
    agent re-pushes). Producers that can't produce a stable key leave it
    null; the row is then always inserted.

    processed_at: set by the trigger dispatcher after the event has been
    examined for matching workflow triggers. Null means "not yet examined."

    payload: event-type-specific. Never contains secrets — producers run
    it through redact_dict() before persisting.

    status: "recorded" | "dispatched" | "ignored"
      recorded   — waiting to be examined by the dispatcher
      dispatched — a workflow was started or matched; details in payload
      ignored    — examined, no trigger matched
    """
    __tablename__ = "events"

    event_type: Mapped[str] = mapped_column(
        String(80), nullable=False, index=True
    )

    source_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("data_sources.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    dedup_key: Mapped[str | None] = mapped_column(
        String(200), nullable=True, index=True
    )

    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="recorded", index=True
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "dedup_key",
            name="uq_events_tenant_dedup",
        ),
        Index("ix_events_tenant_type_occurred", "tenant_id", "event_type", "occurred_at"),
        Index("ix_events_status_received", "status", "received_at"),
    )