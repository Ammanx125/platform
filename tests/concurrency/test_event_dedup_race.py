# tests/concurrency/test_event_dedup_race.py
"""
Race: two callers record the same dedup_key at the same time.

Invariant: exactly one Event row exists for (tenant_id, dedup_key), and
both callers get the same row back (one with created=True, one with
created=False, or both with the same row depending on timing).

No IntegrityError is raised. This is the whole point of ON CONFLICT.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models.event import Event
from app.db.session import SessionLocal
from app.services.events import store as events_store

pytestmark = pytest.mark.concurrency


@pytest.mark.asyncio
async def test_concurrent_dedup_same_key(concurrency_env: dict) -> None:
    tenant_a = concurrency_env["tenant_a"]
    dedup_key = "test-dedup-race-1"

    from tests.concurrency.helpers import run_concurrently

    async def _record():
        async with SessionLocal() as db:
            event, created = await events_store.record_event(
                db,
                tenant_id=tenant_a,
                event_type="file.observed",
                payload={"path": "/tmp/x"},
                dedup_key=dedup_key,
            )
            await db.commit()
            return event.id, created

    results = await run_concurrently([_record, _record, _record])

    assert all(r.succeeded for r in results)

    ids = {r.value[0] for r in results}
    created_flags = [r.value[1] for r in results]

    # All three callers see the same Event row.
    assert len(ids) == 1
    # Exactly one caller actually inserted.
    assert sum(created_flags) == 1

    # Post-condition: exactly one row exists.
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(Event).where(
                    Event.tenant_id == tenant_a,
                    Event.dedup_key == dedup_key,
                )
            )
        ).scalars().all()
        assert len(rows) == 1