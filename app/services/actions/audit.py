# app/services/actions/audit.py
"""
Audit event emission for the action pipeline.

Kept separate from the action service so that (a) the emission pattern is
in one place and (b) later steps (Step 19) can expand the audit surface
without touching the action pipeline itself.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.audit import AuditEvent


async def emit_event(
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
    Append one audit event. Does not commit — the caller commits as part
    of the surrounding transaction, so an action's events roll back
    atomically with the action itself.
    """
    event = AuditEvent(
        tenant_id=tenant_id,
        event_type=event_type,
        actor_user_id=actor_user_id,
        subject_type=subject_type,
        subject_id=subject_id,
        event_metadata=dict(metadata or {}),
        message=message,
    )
    db.add(event)
    await db.flush()
    return event