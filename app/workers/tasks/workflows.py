# app/workers/tasks/workflows.py
"""
Workflow tasks.

Two tasks:

  run_pending_workflows_once — starts a pending WorkflowInstance (one
    whose status is "pending"). In practice the current create flow uses
    run_now=True and never leaves pending instances; this task is a
    safety net and the hook for future run_now=False creation (e.g.
    schedule-triggered).

  run_waiting_workflows_once — resumes a WorkflowInstance in
    waiting_action_approval whose referenced ActionRecord has reached a
    terminal state. This is what makes the workflow complete without a
    human calling the resume endpoint.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.action import ActionRecord
from app.db.models.user import User
from app.db.models.workflow import WorkflowInstance
from app.services.workflows.base import WorkflowContext
from app.services.workflows.engine import resume_instance, run_instance


async def _workflow_user_id(
    db: AsyncSession, instance: WorkflowInstance
) -> uuid.UUID | None:
    if instance.triggered_by_user_id is not None:
        return instance.triggered_by_user_id
    system_user = (
        await db.execute(
            select(User).where(
                User.tenant_id == instance.tenant_id,
                User.is_system.is_(True),
            )
        )
    ).scalar_one_or_none()
    return system_user.id if system_user is not None else None


async def run_pending_workflows_once(db: AsyncSession) -> int:
    stmt = (
        select(WorkflowInstance)
        .where(WorkflowInstance.status == "pending")
        .order_by(WorkflowInstance.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    instance = (await db.execute(stmt)).scalar_one_or_none()
    if instance is None:
        return 0

    user_id = await _workflow_user_id(db, instance)
    if user_id is None:
        instance.status = "failed"
        instance.error = "no system user for tenant"
        await db.flush()
        return 1

    context = WorkflowContext(
        tenant_id=instance.tenant_id,
        user_id=user_id,
        decision_run_id=instance.decision_run_id,
        source_ids=[],
        bag=dict(instance.workflow_context),
    )
    await run_instance(db, instance=instance, context=context)
    return 1


async def run_waiting_workflows_once(db: AsyncSession) -> int:
    # Find a waiting_action_approval instance whose action is terminal.
    terminal = {"executed", "failed", "verification_failed", "rejected"}
    stmt = (
        select(WorkflowInstance, ActionRecord)
        .join(ActionRecord, ActionRecord.id == WorkflowInstance.pending_action_id)
        .where(
            WorkflowInstance.status == "waiting_action_approval",
            ActionRecord.status.in_(terminal),
        )
        .order_by(WorkflowInstance.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    row = (await db.execute(stmt)).first()
    if row is None:
        return 0
    instance, _action = row

    user_id = await _workflow_user_id(db, instance)
    if user_id is None:
        instance.status = "failed"
        instance.error = "no system user for tenant"
        await db.flush()
        return 1

    await resume_instance(
        db,
        instance=instance,
        user_id=user_id,
    )
    return 1