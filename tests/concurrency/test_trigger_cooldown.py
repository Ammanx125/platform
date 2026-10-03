# tests/concurrency/test_trigger_cooldown_race.py
"""
Race: two dispatcher iterations try to fire the same trigger.

Invariant: exactly one claim succeeds, and the trigger fires once.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.db.models.workflow import WorkflowTrigger
from app.db.session import SessionLocal
from app.services.events import dispatcher

pytestmark = pytest.mark.concurrency


async def _seed_trigger(tenant_id) -> uuid.UUID:
    async with SessionLocal() as db:
        trigger = WorkflowTrigger(
            tenant_id=tenant_id,
            workflow_key="procurement.spend_analysis",
            event_type_filter="*",
            enabled=True,
            cooldown_seconds=3600,
            trigger_params={},
            last_fired_at=None,
        )
        db.add(trigger)
        await db.commit()
        await db.refresh(trigger)
        return trigger.id


@pytest.mark.asyncio
async def test_concurrent_cooldown_claim(concurrency_env: dict) -> None:
    tenant_a = concurrency_env["tenant_a"]
    trigger_id = await _seed_trigger(tenant_a)
    now = datetime.now(UTC)

    from tests.concurrency.helpers import (
        count_failures,
        run_concurrently,
    )

    async def _claim():
        async with SessionLocal() as db:
            claimed = await dispatcher._try_claim_trigger(
                db,
                trigger_id=trigger_id,
                tenant_id=tenant_a,
                now=now,
            )
            await db.commit()
            return claimed is not None

    results = await run_concurrently([_claim, _claim, _claim])

    assert count_failures(results) == 0
    # Exactly one call returned True (claimed); the rest returned False.
    assert sum(1 for r in results if r.value) == 1