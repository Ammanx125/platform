# tests/concurrency/test_workflow_approval_race.py
"""
Race: two approvers approve the same workflow instance at the same time.

Invariant: exactly one transition from waiting_workflow_approval to
resumed happens. The other gets WorkflowError.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.models.workflow import WorkflowInstance
from app.db.session import SessionLocal
from app.services.workflows import engine as workflow_engine
from app.services.workflows.engine import WorkflowError

pytestmark = pytest.mark.concurrency


async def _seed_waiting_workflow(tenant_id, proposer_user_id) -> uuid.UUID:
    """
    Insert a WorkflowInstance directly in waiting_workflow_approval state.
    The test workflow is registered by the matching step-state helper.
    """
    from tests.concurrency.helpers import (
        CONCURRENCY_APPROVAL_WORKFLOW_KEY,
        waiting_approval_step_states,
    )

    async with SessionLocal() as db:
        instance = WorkflowInstance(
            tenant_id=tenant_id,
            workflow_key=CONCURRENCY_APPROVAL_WORKFLOW_KEY,
            status="waiting_workflow_approval",
            step_states=waiting_approval_step_states(),
            workflow_context={},
            triggered_by_user_id=proposer_user_id,
            trigger_type="manual",
            trigger_metadata={},
            pending_approval_step="approval",
        )
        db.add(instance)
        await db.commit()
        await db.refresh(instance)
        return instance.id


async def _approve(instance_id: uuid.UUID, tenant_id: uuid.UUID, approver_id: uuid.UUID):
    async with SessionLocal() as db:
        inst = await workflow_engine.approve_instance(
            db,
            instance_id=instance_id,
            tenant_id=tenant_id,
            approver_user_id=approver_id,
        )
        await db.commit()
        return inst


@pytest.mark.asyncio
async def test_two_concurrent_workflow_approvals(concurrency_env: dict) -> None:
    tenant_a = concurrency_env["tenant_a"]
    instance_id = await _seed_waiting_workflow(
        tenant_a, concurrency_env["user_a"]
    )

    from tests.concurrency.helpers import (
        count_failures,
        count_successes,
        run_concurrently,
    )

    results = await run_concurrently([
        lambda: _approve(instance_id, tenant_a, concurrency_env["user_a2"]),
        lambda: _approve(instance_id, tenant_a, concurrency_env["user_a2"]),
    ])

    assert count_successes(results) == 1
    assert count_failures(results) == 1

    failed = next(r for r in results if not r.succeeded)
    assert isinstance(failed.exception, WorkflowError)

    async with SessionLocal() as db:
        row = (
            await db.execute(
                select(WorkflowInstance).where(WorkflowInstance.id == instance_id)
            )
        ).scalar_one()
        # Must be terminal or running, not still waiting.
        assert row.status != "waiting_workflow_approval"