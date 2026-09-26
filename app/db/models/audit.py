# app/db/models/audit.py
"""
Audit events.

An AuditEvent is a durable, append-only record of a security- or
business-relevant action taken in the platform. It is deliberately small
and categorical: the *full* reconstruction of a decision lives in
DecisionRun, and the *full* reconstruction of an action lives in
ActionRecord. AuditEvent indexes the events that matter most.

Immutability: the service layer never updates or deletes AuditEvent rows.
A Postgres-level constraint (trigger rejecting UPDATE/DELETE) is deferred
to Step 19 (audit hardening), because it requires a migration wrinkle that
isn't worth taking on now. Treat this as an app-level invariant.
"""
from __future__ import annotations

import uuid

from sqlalchemy import Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class AuditEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin, TenantMixin):
    """
    One audit event.

    event_type:  dotted category, e.g. "action.proposed", "action.approved",
                 "action.rejected", "action.executed",
                 "action.verification_failed", "document.deleted",
                 "source.connected", "user.invited".
    subject_type/subject_id: what the event is about (an action, a
                 document, a user). Nullable when the event is about the
                 tenant as a whole.
    actor_user_id: who triggered the event. Nullable for system events.
    metadata:    structured payload for the event. Never contains secrets.
    """
    __tablename__ = "audit_events"

    event_type: Mapped[str] = mapped_column(
        String(80), nullable=False, index=True
    )
    subject_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True, index=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True, index=True
    )

    event_metadata: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    message: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_audit_events_tenant_type", "tenant_id", "event_type"),
        Index("ix_audit_events_tenant_created", "tenant_id", "created_at"),
    )