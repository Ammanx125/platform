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
from app.services.actions.audit import emit_event
from app.services.actions.service import execute_approved


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

    Sets status to 'approved', then runs the execution pipeline (which
    flips status to 'executed', 'failed', or 'verification_failed').
    Emits action.approved before execution.
    """
    record = await _load_pending(db, action_id=action_id, tenant_id=tenant_id)

    if record.user_id == approver_user_id:
        raise ApprovalError("the proposer cannot approve their own action")

    record.approved_by_user_id = approver_user_id
    record.approved_at = datetime.now(UTC)
    record.status = "approved"
    await db.flush()

    await emit_event(
        db,
        tenant_id=tenant_id,
        event_type="action.approved",
        actor_user_id=approver_user_id,
        subject_type="action",
        subject_id=record.id,
        metadata={"tool_name": record.tool_name},
    )

    await execute_approved(db, record=record, actor_user_id=approver_user_id)
    return record


async def reject(
    db: AsyncSession,
    *,
    action_id: uuid.UUID,
    tenant_id: uuid.UUID,
    rejector_user_id: uuid.UUID,
    reason: str | None = None,
) -> ActionRecord:
    """
    Reject a pending action.

    Sets status to 'rejected' with the supplied reason. No execution.
    """
    record = await _load_pending(db, action_id=action_id, tenant_id=tenant_id)

    if record.user_id == rejector_user_id:
        raise ApprovalError("the proposer cannot reject their own action")

    record.status = "rejected"
    record.rejection_reason = reason or "rejected by approver"
    await db.flush()

    await emit_event(
        db,
        tenant_id=tenant_id,
        event_type="action.rejected",
        actor_user_id=rejector_user_id,
        subject_type="action",
        subject_id=record.id,
        metadata={
            "tool_name": record.tool_name,
            "reason": record.rejection_reason,
        },
        message=record.rejection_reason,
    )
    return record