from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user import User
from app.db.models.workflow import WorkflowInstance
from app.db.session import SessionLocal
from app.workers.tasks import workflows


@pytest_asyncio.fixture
async def db(two_tenants: dict) -> AsyncGenerator[AsyncSession]:
    _ = two_tenants
    async with SessionLocal() as session:
        yield session


@pytest.mark.asyncio
async def test_pending_workflow_uses_system_user(db, two_tenants, monkeypatch):
    system_user = User(
        tenant_id=two_tenants["tenant_a"],
        email=f"system+{two_tenants['tenant_a']}@sansa.local",
        password_hash="unused",
        is_active=True,
        is_system=True,
    )
    instance = WorkflowInstance(
        tenant_id=two_tenants["tenant_a"],
        workflow_key="test.workflow",
        status="pending",
        step_states=[],
        workflow_context={},
        triggered_by_user_id=None,
        trigger_type="event",
        trigger_metadata={},
    )
    db.add_all([system_user, instance])
    await db.flush()
    received_user_ids = []

    async def fake_run_instance(_db, *, instance, context):
        received_user_ids.append(context.user_id)

    monkeypatch.setattr(workflows, "run_instance", fake_run_instance)

    processed = await workflows.run_pending_workflows_once(db)

    assert processed == 1
    assert received_user_ids == [system_user.id]


@pytest.mark.asyncio
async def test_pending_workflow_fails_when_system_user_is_missing(db, two_tenants):
    instance = WorkflowInstance(
        tenant_id=two_tenants["tenant_a"],
        workflow_key="test.workflow",
        status="pending",
        step_states=[],
        workflow_context={},
        triggered_by_user_id=None,
        trigger_type="event",
        trigger_metadata={},
    )
    db.add(instance)
    await db.flush()

    processed = await workflows.run_pending_workflows_once(db)

    assert processed == 1
    assert instance.status == "failed"
    assert instance.error == "no system user for tenant"