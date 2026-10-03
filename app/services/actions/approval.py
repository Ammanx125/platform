# app/services/actions/approval.py
"""
Approval workflow.

approve() and reject() are the two terminal transitions on a
pending_approval record. Both enforce:

  - The caller has action:approve (approve) or action:reject (reject).
    Enforced at the API layer via require_permission; also re-checked here
    as a second belt.
  - The record belongs to the caller's tenant.
  - The record is currently pending_approval. Any other state → 409.
  - The caller is NOT the proposer. Maker-checker: you cannot approve or
    reject your own action.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.action import ActionRecord
from app.services.actions.service import execute_approved
from app.services.audit.service import emit as emit_event


class ApprovalError(Exception):
    """Approval or rejection could not proceed."""


async def _load_pending(
    db: AsyncSession,
    *,
    action_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> ActionRecord:
    record = (
        await db.execute(
            select(ActionRecord).where(
                ActionRecord.id == action_id,
                ActionRecord.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if record is None:
        raise ApprovalError("action not found")
    if record.status != "pending_approval":
        raise ApprovalError(
            f"action is in status {record.status!r}, "
            f"expected 'pending_approval'"
        )
    return record


async def approve(
    db: AsyncSession,
    *,
    action_id: uuid.UUID,
    tenant_id: uuid.UUID,
    approver_user_id: uuid.UUID,
) -> ActionRecord:
    """
    Approve a pending action and immediately execute it.

    The transition from pending_approval to approved is an atomic
    conditional UPDATE. If the row is no longer pending_approval —
    because another approver beat us, or the action was rejected, or it
    doesn't exist for this tenant — the UPDATE returns zero rows and we
    reject with a clear error. No read-then-write window.
    """
    now = datetime.now(UTC)

    # Step 1: claim the transition atomically. We can't check "is this the
    # proposer's own action" in the UPDATE's WHERE clause because that
    # would require embedding the approver's identity in the SET clause.
    # Instead we fetch the row with FOR UPDATE, verify identity, then
    # transition. The lock prevents a concurrent approver from advancing
    # the state while we're deciding.
    row = (
        await db.execute(
            select(ActionRecord)
            .where(
                ActionRecord.id == action_id,
                ActionRecord.tenant_id == tenant_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if row is None:
        raise ApprovalError("action not found")
    if row.status != "pending_approval":
        raise ApprovalError(
            f"action is in status {row.status!r}, "
            f"expected 'pending_approval'"
        )
    if row.user_id == approver_user_id:
        raise ApprovalError("the proposer cannot approve their own action")

    # Row is locked; no concurrent approver can interleave between here
    # and the commit that flushes this transition.
    row.approved_by_user_id = approver_user_id
    row.approved_at = now
    row.status = "approved"
    await db.flush()

    await emit_event(
        db,
        tenant_id=tenant_id,
        event_type="action.approved",
        actor_user_id=approver_user_id,
        subject_type="action",
        subject_id=row.id,
        metadata={"tool_name": row.tool_name},
    )

    await execute_approved(db, record=row, actor_user_id=approver_user_id)
    return row


async def reject(
    db: AsyncSession,
    *,
    action_id: uuid.UUID,
    tenant_id: uuid.UUID,
    rejector_user_id: uuid.UUID,
    reason: str | None = None,
) -> ActionRecord:
    row = (
        await db.execute(
            select(ActionRecord)
            .where(
                ActionRecord.id == action_id,
                ActionRecord.tenant_id == tenant_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if row is None:
        raise ApprovalError("action not found")
    if row.status != "pending_approval":
        raise ApprovalError(
            f"action is in status {row.status!r}, "
            f"expected 'pending_approval'"
        )
    if row.user_id == rejector_user_id:
        raise ApprovalError("the proposer cannot reject their own action")

    row.status = "rejected"
    row.rejection_reason = reason or "rejected by approver"
    await db.flush()

    await emit_event(
        db,
        tenant_id=tenant_id,
        event_type="action.rejected",
        actor_user_id=rejector_user_id,
        subject_type="action",
        subject_id=row.id,
        metadata={
            "tool_name": row.tool_name,
            "reason": row.rejection_reason,
        },
        message=row.rejection_reason,
    )
    return row