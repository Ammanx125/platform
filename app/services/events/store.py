# app/services/events/store.py
"""
Event recording and retrieval.

record_event() is the single write path. It redacts the payload, applies
deduplication, and flushes. It does not commit — the caller commits as
part of the surrounding transaction, so an event rolls back with the
thing that produced it.

Idempotency:
  - If dedup_key is set and an Event with (tenant_id, dedup_key) already
    exists, the new row is NOT inserted and the existing row is returned.
  - If dedup_key is None, the event is always inserted (callers that can't
    produce stable keys accept the risk of duplicates).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.event import Event
from app.services.security.redaction import redact_dict


async def record_event(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any] | None = None,
    source_id: uuid.UUID | None = None,
    occurred_at: datetime | None = None,
    dedup_key: str | None = None,
) -> tuple[Event, bool]:
    """
    Record one event. Returns (event, created).

    created is False when a deduplicated event already existed; the returned
    Event is the existing one.
    """
    if dedup_key is not None:
        existing = (
            await db.execute(
                select(Event).where(
                    Event.tenant_id == tenant_id,
                    Event.dedup_key == dedup_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing, False

    now = datetime.now(UTC)
    event = Event(
        tenant_id=tenant_id,
        event_type=event_type,
        source_id=source_id,
        occurred_at=occurred_at or now,
        received_at=now,
        dedup_key=dedup_key,
        payload=redact_dict(dict(payload or {})),
        status="recorded",
    )
    db.add(event)
    await db.flush()
    return event, True


async def list_events(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event_type: str | None = None,
    source_id: uuid.UUID | None = None,
    since: datetime | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[Event]:
    stmt = (
        select(Event)
        .where(Event.tenant_id == tenant_id)
        .order_by(Event.received_at.desc())
        .limit(limit)
    )
    if event_type is not None:
        stmt = stmt.where(Event.event_type == event_type)
    if source_id is not None:
        stmt = stmt.where(Event.source_id == source_id)
    if since is not None:
        stmt = stmt.where(Event.received_at >= since)
    if status is not None:
        stmt = stmt.where(Event.status == status)
    return list((await db.execute(stmt)).scalars().all())


async def get_event(
    db: AsyncSession, *, event_id: uuid.UUID, tenant_id: uuid.UUID
) -> Event | None:
    return (
        await db.execute(
            select(Event).where(
                Event.id == event_id,
                Event.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()