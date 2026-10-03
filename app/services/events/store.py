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
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.event import Event
from app.services.security.redaction import redact_dict

_ALLOWED_ACTOR_KINDS = frozenset({"user", "system", "service"})


async def record_event(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any] | None = None,
    source_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    actor_kind: str = "system",
    occurred_at: datetime | None = None,
    dedup_key: str | None = None,
) -> tuple[Event, bool]:
    """
    Record one event. Returns (event, created).

    Concurrency: when dedup_key is set, the insert uses ON CONFLICT DO
    NOTHING against the (tenant_id, dedup_key) unique constraint. If
    another request inserted the same key first, the INSERT returns no
    rows; we then SELECT the existing row and return it with created=False.

    This is atomic at the DB level. There is no read-then-write window.
    """
    if actor_kind not in _ALLOWED_ACTOR_KINDS:
        raise ValueError(
            f"actor_kind must be one of {sorted(_ALLOWED_ACTOR_KINDS)}, "
            f"got {actor_kind!r}"
        )

    now = datetime.now(UTC)
    event = Event(
        tenant_id=tenant_id,
        event_type=event_type,
        source_id=source_id,
        user_id=user_id,
        actor_kind=actor_kind,
        occurred_at=occurred_at or now,
        received_at=now,
        dedup_key=dedup_key,
        payload=redact_dict(dict(payload or {})),
        status="recorded",
    )

    if dedup_key is None:
        db.add(event)
        await db.flush()
        return event, True

    # Dedup-keyed insert: rely on the unique constraint.
    stmt = (
        pg_insert(Event)
        .values(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            event_type=event_type,
            source_id=source_id,
            user_id=user_id,
            actor_kind=actor_kind,
            occurred_at=event.occurred_at,
            received_at=event.received_at,
            dedup_key=dedup_key,
            payload=event.payload,
            status="recorded",
        )
        .on_conflict_do_nothing(
            index_elements=["tenant_id", "dedup_key"]
        )
        .returning(Event)
    )
    result = await db.execute(stmt)
    inserted = result.scalar_one_or_none()
    if inserted is not None:
        return inserted, True

    # Conflict: another request inserted the same (tenant, dedup_key).
    existing = (
        await db.execute(
            select(Event).where(
                Event.tenant_id == tenant_id,
                Event.dedup_key == dedup_key,
            )
        )
    ).scalar_one()
    return existing, False


async def list_events(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event_type: str | None = None,
    source_id: uuid.UUID | None = None,
    actor_kind: str | None = None,
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
    if actor_kind is not None:
        stmt = stmt.where(Event.actor_kind == actor_kind)
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