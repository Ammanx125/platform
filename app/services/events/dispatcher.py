# app/services/events/dispatcher.py
"""
Event dispatcher.

Matches unprocessed Events against WorkflowTrigger rows and starts
workflows. This is the direct event → workflow path (blueprint section 23,
simplified for v1; the "state updater" tier is deferred).

Cooldown: a trigger has a cooldown_seconds window. If a matching event
arrives within the cooldown of the last time this trigger fired, the
trigger is skipped (but the event is still marked processed). This
prevents a burst of events from spawning many workflow runs.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.event import Event
from app.db.models.user import User
from app.db.models.workflow import WorkflowTrigger
from app.services.workflows.engine import WorkflowError, start_workflow


async def dispatch_once(db: AsyncSession, *, batch: int = 10) -> int:
    """
    Process up to `batch` unprocessed events. Returns the number of events
    examined (not the number of workflows started).
    """
    stmt = (
        select(Event)
        .where(Event.status == "recorded")
        .order_by(Event.received_at.asc())
        .limit(batch)
        .with_for_update(skip_locked=True)
    )
    events = (await db.execute(stmt)).scalars().all()
    if not events:
        return 0

    now = datetime.now(UTC)
    dispatched = 0
    system_user_ids: dict[uuid.UUID, uuid.UUID | None] = {}

    async def system_user_id_for(tenant_id: uuid.UUID) -> uuid.UUID | None:
        if tenant_id in system_user_ids:
            return system_user_ids[tenant_id]
        user = (
            await db.execute(
                select(User).where(
                    User.tenant_id == tenant_id,
                    User.is_system.is_(True),
                )
            )
        ).scalar_one_or_none()
        user_id = user.id if user is not None else None
        system_user_ids[tenant_id] = user_id
        return user_id

    for event in events:
        matched = await _matching_triggers(db, event=event)
        started_any = False
        if not matched:
            event.status = "ignored"
            event.processed_at = now
            dispatched += 1
            continue

        user_id = await system_user_id_for(event.tenant_id)

        if user_id is None:
            event.status = "ignored"
            event.processed_at = now
            event.error = "no system user for tenant"
            dispatched += 1
            continue

        for trigger in matched:
            claimed = await _try_claim_trigger(
                db,
                trigger_id=trigger.id,
                tenant_id=event.tenant_id,
                now=now,
            )
            if claimed is None:
                continue
            try:
                await start_workflow(
                    db,
                    workflow_key=claimed.workflow_key,
                    tenant_id=event.tenant_id,
                    user_id=user_id,
                    source_ids=_resolve_source_ids(claimed, event),
                    trigger_type="event",
                    trigger_metadata={
                        "event_id": str(event.id),
                        "event_type": event.event_type,
                        "trigger_id": str(claimed.id),
                        **dict(claimed.trigger_params or {}),
                    },
                    decision_run_id=None,
                    run_now=True,
                )
                started_any = True
            except WorkflowError as exc:
                if event.error:
                    event.error = f"{event.error}; {exc}"[:500]
                else:
                    event.error = str(exc)[:500]
            except Exception as exc:  # noqa: BLE001
                if event.error:
                    event.error = f"{event.error}; {exc}"[:500]
                else:
                    event.error = str(exc)[:500]

        event.status = "dispatched" if started_any else "ignored"
        event.processed_at = now
        dispatched += 1

    await db.flush()
    return dispatched


async def _matching_triggers(
    db: AsyncSession, *, event: Event
) -> list[WorkflowTrigger]:
    stmt = (
        select(WorkflowTrigger)
        .where(
            WorkflowTrigger.tenant_id == event.tenant_id,
            WorkflowTrigger.enabled.is_(True),
        )
    )
    triggers = (await db.execute(stmt)).scalars().all()
    return [
        t for t in triggers
        if (
            t.event_type_filter == "*"
            or t.event_type_filter == event.event_type
        )
        and (
            t.source_id_filter is None
            or t.source_id_filter == event.source_id
        )
    ]


async def _try_claim_trigger(
    db: AsyncSession,
    *,
    trigger_id: uuid.UUID,
    tenant_id: uuid.UUID,
    now: datetime,
) -> WorkflowTrigger | None:
    """Atomically claim a trigger cooldown window if it has elapsed."""
    stmt = (
        update(WorkflowTrigger)
        .where(
            WorkflowTrigger.id == trigger_id,
            WorkflowTrigger.tenant_id == tenant_id,
            WorkflowTrigger.enabled.is_(True),
            or_(
                WorkflowTrigger.last_fired_at.is_(None),
                WorkflowTrigger.last_fired_at
                + func.make_interval(0, 0, 0, 0, 0, 0, WorkflowTrigger.cooldown_seconds)
                <= now,
            ),
        )
        .values(last_fired_at=now)
        .returning(WorkflowTrigger)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


def _resolve_source_ids(
    trigger: WorkflowTrigger, event: Event
) -> list:
    explicit = trigger.trigger_params.get("source_ids")
    if explicit:
        return list(explicit)
    if event.source_id:
        return [event.source_id]
    return []