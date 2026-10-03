# tests/concurrency/test_action_approval_race.py
"""
Race: two managers approve the same action at the same time.

Invariant: exactly one transition from pending_approval to approved
happens, and exactly one `action.approved` audit event is written.
The other approver gets ApprovalError.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.db.models.action import ActionRecord
from app.db.models.audit import AuditEvent
from app.db.session import SessionLocal
from app.services.actions import approval as approval_service
from app.services.actions.approval import ApprovalError
from app.services.audit import types as audit_types
from tests.concurrency.helpers import (
    count_failures,
    count_successes,
    run_concurrently,
)

pytestmark = pytest.mark.concurrency


async def _seed_pending_action(tenant_id, proposer_user_id) -> uuid.UUID:
    async with SessionLocal() as db:
        record = ActionRecord(
            tenant_id=tenant_id,
            user_id=proposer_user_id,
            tool_name="generate_report",
            arguments={"title": "race", "body": "x" * 50},
            status="pending_approval",
            proposed_at=datetime.now(UTC),
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)
        return record.id


async def _approve(action_id: uuid.UUID, tenant_id: uuid.UUID, approver_id: uuid.UUID):
    async with SessionLocal() as db:
        record = await approval_service.approve(
            db,
            action_id=action_id,
            tenant_id=tenant_id,
            approver_user_id=approver_id,
        )
        await db.commit()
        return record


@pytest.mark.asyncio
async def test_two_concurrent_approvals_exactly_one_wins(
    concurrency_env: dict,
) -> None:
    tenant_a = concurrency_env["tenant_a"]
    # Proposer is user_a. Approver must be user_a2 (maker-checker).
    action_id = await _seed_pending_action(tenant_a, concurrency_env["user_a"])

    results = await run_concurrently([
        lambda: _approve(action_id, tenant_a, concurrency_env["user_a2"]),
        lambda: _approve(action_id, tenant_a, concurrency_env["user_a2"]),
    ])

    assert count_successes(results) == 1
    assert count_failures(results) == 1

    # The failure must be an ApprovalError about state.
    failed = next(r for r in results if not r.succeeded)
    assert isinstance(failed.exception, ApprovalError)
    msg = str(failed.exception).lower()
    assert "pending_approval" in msg or "status" in msg or "not found" in msg

    # Post-condition: the row is in a terminal state, not "approved".
    async with SessionLocal() as db:
        row = (
            await db.execute(
                select(ActionRecord).where(ActionRecord.id == action_id)
            )
        ).scalar_one()
        assert row.status in ("executed", "failed", "verification_failed")

    # Exactly one action.approved audit event.
    async with SessionLocal() as db:
        events = (
            await db.execute(
                select(AuditEvent).where(
                    AuditEvent.tenant_id == tenant_a,
                    AuditEvent.subject_id == action_id,
                    AuditEvent.event_type == audit_types.ACTION_APPROVED,
                )
            )
        ).scalars().all()
        assert len(events) == 1


@pytest.mark.asyncio
async def test_approve_and_reject_race(concurrency_env: dict) -> None:
    """
    Two managers: one approves, one rejects, at the same time.

    Exactly one of those transitions must happen. The other loses.
    """
    tenant_a = concurrency_env["tenant_a"]
    action_id = await _seed_pending_action(tenant_a, concurrency_env["user_a"])

    async def _reject(aid, tid, uid):
        async with SessionLocal() as db:
            r = await approval_service.reject(
                db, action_id=aid, tenant_id=tid, rejector_user_id=uid,
                reason="race",
            )
            await db.commit()
            return r

    results = await run_concurrently([
        lambda: _approve(action_id, tenant_a, concurrency_env["user_a2"]),
        lambda: _reject(action_id, tenant_a, concurrency_env["user_a2"]),
    ])

    assert count_successes(results) == 1
    assert count_failures(results) == 1

    # Post-condition: the row is terminal.
    async with SessionLocal() as db:
        row = (
            await db.execute(
                select(ActionRecord).where(ActionRecord.id == action_id)
            )
        ).scalar_one()
        assert row.status in (
            "executed", "failed", "verification_failed", "rejected"
        )