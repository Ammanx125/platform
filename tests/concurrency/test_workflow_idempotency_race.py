# tests/concurrency/test_workflow_idempotency_race.py
"""
Race: two callers start the same workflow with the same idempotency key.

Invariant: exactly one WorkflowInstance is created for (tenant, key).
Both callers get the same instance row.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models.workflow import WorkflowInstance
from app.db.session import SessionLocal
from app.services.workflows import engine as workflow_engine

pytestmark = pytest.mark.concurrency


@pytest.mark.asyncio
async def test_concurrent_idempotent_start(concurrency_env: dict) -> None:
    tenant_a = concurrency_env["tenant_a"]
    user_a = concurrency_env["user_a"]
    key = "race-idempotency-1"

    from tests.concurrency.helpers import run_concurrently

    async def _start():
        async with SessionLocal() as db:
            inst = await workflow_engine.start_workflow(
                db,
                workflow_key="procurement.spend_analysis",
                tenant_id=tenant_a,
                user_id=user_a,
                run_now=False,
                idempotency_key=key,
            )
            await db.commit()
            return inst.id

    results = await run_concurrently([_start, _start, _start])

    assert all(r.succeeded for r in results)

    ids = {r.value for r in results}
    assert len(ids) == 1

    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(WorkflowInstance).where(
                    WorkflowInstance.tenant_id == tenant_a,
                    WorkflowInstance.idempotency_key == key,
                )
            )
        ).scalars().all()
        assert len(rows) == 1