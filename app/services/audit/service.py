# app/services/audit/service.py
"""
Centralized audit event emission.

Single write path for AuditEvent rows. All emitters (actions, ingestion,
workflows, decisions) call emit() rather than constructing AuditEvent
directly, so the construction contract, redaction, and validation are in
one place.

Transaction contract: emit() does NOT commit. It adds and flushes only.
The audit event therefore rolls back with the state change it describes,
so an audit record cannot claim success for a transition that later
rolled back. Callers must emit inside the same transaction that mutates
the state they are auditing.

The emit() call must be placed AFTER the state change is set on the ORM
object, so the same flush that would persist the state change also
persists the audit event.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.audit import AuditEvent
from app.services.audit.types import ALL_AUDIT_EVENT_TYPES
from app.services.security.redaction import redact_dict


def _validate_event_type(event_type: str) -> None:
    """
    Reject unknown audit event types. Prevents drift from the audit
    taxonomy defined in types.py. If you need a new event type, add it
    to the constants module — do not pass arbitrary strings.
    """
    if event_type not in ALL_AUDIT_EVENT_TYPES:
        raise ValueError(
            f"unknown audit event_type: {event_type!r}. "
            f"Add it to app/services/audit/types.py first."
        )


async def emit(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event_type: str,
    actor_user_id: uuid.UUID | None = None,
    subject_type: str | None = None,
    subject_id: uuid.UUID | None = None,
    metadata: dict[str, Any] | None = None,
    message: str | None = None,
) -> AuditEvent:
    """
    Append one audit event. Does not commit.

    Raises ValueError if event_type is not in the known audit taxonomy.
    """
    _validate_event_type(event_type)
    event = AuditEvent(
        tenant_id=tenant_id,
        event_type=event_type,
        actor_user_id=actor_user_id,
        subject_type=subject_type,
        subject_id=subject_id,
        event_metadata=redact_dict(dict(metadata or {})),
        message=message,
    )
    db.add(event)
    await db.flush()
    return event


async def list_events(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event_type: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    subject_type: str | None = None,
    subject_id: uuid.UUID | None = None,
    since=None,
    before=None,
    limit: int = 100,
) -> list[AuditEvent]:
    """Read-only listing, used by the audit view."""
    from sqlalchemy import select
    stmt = (
        select(AuditEvent)
        .where(AuditEvent.tenant_id == tenant_id)
        .order_by(AuditEvent.created_at.desc())
        .limit(limit)
    )
    if event_type is not None:
        stmt = stmt.where(AuditEvent.event_type == event_type)
    if actor_user_id is not None:
        stmt = stmt.where(AuditEvent.actor_user_id == actor_user_id)
    if subject_type is not None:
        stmt = stmt.where(AuditEvent.subject_type == subject_type)
    if subject_id is not None:
        stmt = stmt.where(AuditEvent.subject_id == subject_id)
    if since is not None:
        stmt = stmt.where(AuditEvent.created_at >= since)
    if before is not None:
        stmt = stmt.where(AuditEvent.created_at < before)
    return list((await db.execute(stmt)).scalars().all())