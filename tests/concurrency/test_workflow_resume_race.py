# tests/concurrency/test_workflow_resume_race.py
"""
Race: a worker's resume and a user's manual resume fire at the same time.

Invariant: exactly one resume succeeds.
"""
from __future__ import annotations

import uuid

import pytest

from app.db.models.workflow import WorkflowInstance
from app.db.session import SessionLocal
from app.services.workflows import engine as workflow_engine
from app.services.workflows.engine import WorkflowError
from tests.concurrency.helpers import (
    CONCURRENCY_APPROVAL_WORKFLOW_KEY,
    waiting_approval_step_states,
)

pytestmark = pytest.mark.concurrency


async def _seed_waiting(tenant_id, user_id) -> uuid.UUID:
    async with SessionLocal() as db:
        instance = WorkflowInstance(
            tenant_id=tenant_id,
            workflow_key=CONCURRENCY_APPROVAL_WORKFLOW_KEY,
            status="waiting_workflow_approval",
            step_states=waiting_approval_step_states(),
            workflow_context={},
            triggered_by_user_id=user_id,
            trigger_type="manual",
            trigger_metadata={},
            pending_approval_step="approval",
        )
        db.add(instance)
        await db.commit()
        await db.refresh(instance)
        return instance.id


@pytest.mark.asyncio
async def test_concurrent_resume(concurrency_env: dict) -> None:
    tenant_a = concurrency_env["tenant_a"]
    instance_id = await _seed_waiting(tenant_a, concurrency_env["user_a"])

    from tests.concurrency.helpers import (
        count_failures,
        count_successes,
        run_concurrently,
    )

    async def _resume():
        async with SessionLocal() as db:
            inst = await workflow_engine.get_instance(
                db, instance_id=instance_id, tenant_id=tenant_a
            )
            assert inst is not None
            result = await workflow_engine.resume_instance(
                db, instance=inst, user_id=concurrency_env["user_a2"]
            )
            await db.commit()
            return result.id

    results = await run_concurrently([_resume, _resume])

    assert count_successes(results) == 1
    assert count_failures(results) == 1

    failed = next(r for r in results if not r.succeeded)
    assert isinstance(failed.exception, WorkflowError)