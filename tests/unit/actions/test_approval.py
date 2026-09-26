# tests/unit/actions/test_approval.py
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionLocal
from app.services.actions import approval as approval_service
from app.services.actions.approval import ApprovalError


@pytest_asyncio.fixture
async def db(two_tenants: dict) -> AsyncGenerator[AsyncSession]:
    # Close the action session before two_tenants removes its records.
    _ = two_tenants
    async with SessionLocal() as session:
        yield session


@pytest.mark.asyncio
async def test_cannot_approve_own_action(db, two_tenants) -> None:
    """
    Create a fake pending_approval record owned by user_a, then try to
    approve it as user_a.
    """
    from datetime import UTC, datetime

    from app.db.models.action import ActionRecord

    record = ActionRecord(
        tenant_id=two_tenants["tenant_a"],
        user_id=two_tenants["user_a"],
        tool_name="generate_report",
        arguments={"title": "x", "body": "y" * 30},
        status="pending_approval",
        proposed_at=datetime.now(UTC),
    )
    db.add(record)
    await db.flush()

    with pytest.raises(ApprovalError, match="cannot approve their own action"):
        await approval_service.approve(
            db,
            action_id=record.id,
            tenant_id=two_tenants["tenant_a"],
            approver_user_id=two_tenants["user_a"],
        )


@pytest.mark.asyncio
async def test_cannot_approve_non_pending(db, two_tenants) -> None:
    from datetime import UTC, datetime

    from app.db.models.action import ActionRecord

    record = ActionRecord(
        tenant_id=two_tenants["tenant_a"],
        user_id=two_tenants["user_a"],
        tool_name="generate_report",
        arguments={},
        status="rejected",
        proposed_at=datetime.now(UTC),
    )
    db.add(record)
    await db.flush()

    with pytest.raises(ApprovalError, match="expected 'pending_approval'"):
        await approval_service.approve(
            db,
            action_id=record.id,
            tenant_id=two_tenants["tenant_a"],
            approver_user_id=two_tenants["user_b"],
        )


@pytest.mark.asyncio
async def test_reject_sets_reason(db, two_tenants) -> None:
    from datetime import UTC, datetime

    from app.db.models.action import ActionRecord

    record = ActionRecord(
        tenant_id=two_tenants["tenant_a"],
        user_id=two_tenants["user_a"],
        tool_name="generate_report",
        arguments={},
        status="pending_approval",
        proposed_at=datetime.now(UTC),
    )
    db.add(record)
    await db.flush()

    rejected = await approval_service.reject(
        db,
        action_id=record.id,
        tenant_id=two_tenants["tenant_a"],
        rejector_user_id=two_tenants["user_b"],
        reason="not appropriate",
    )
    assert rejected.status == "rejected"
    assert rejected.rejection_reason == "not appropriate"