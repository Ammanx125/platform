from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user import User
from app.db.models.workflow import WorkflowTrigger
from app.db.session import SessionLocal
from app.services.events import dispatcher, store, types


@pytest_asyncio.fixture
async def db(two_tenants: dict) -> AsyncGenerator[AsyncSession]:
    _ = two_tenants
    async with SessionLocal() as session:
        yield session


@pytest.mark.asyncio
async def test_dispatch_uses_tenant_system_user(db, two_tenants, monkeypatch):
    system_user = User(
        tenant_id=two_tenants["tenant_a"],
        email=f"system+{two_tenants['tenant_a']}@sansa.local",
        password_hash="unused",
        full_name="System",
        is_active=True,
        is_system=True,
    )
    trigger = WorkflowTrigger(
        tenant_id=two_tenants["tenant_a"],
        workflow_key="test.workflow",
        event_type_filter=types.INGESTION_COMPLETED,
        enabled=True,
        cooldown_seconds=0,
    )
    db.add_all([system_user, trigger])
    await db.flush()

    event, _ = await store.record_event(
        db,
        tenant_id=two_tenants["tenant_a"],
        event_type=types.INGESTION_COMPLETED,
        payload={},
    )
    workflow_calls = []

    async def fake_start_workflow(_db, **kwargs):
        workflow_calls.append(kwargs)

    monkeypatch.setattr(dispatcher, "start_workflow", fake_start_workflow)

    examined = await dispatcher.dispatch_once(db, batch=10)

    assert examined >= 1
    assert workflow_calls[0]["user_id"] == system_user.id
    assert event.status == "dispatched"


@pytest.mark.asyncio
async def test_dispatch_ignores_event_without_system_user(db, two_tenants):
    trigger = WorkflowTrigger(
        tenant_id=two_tenants["tenant_a"],
        workflow_key="test.workflow",
        event_type_filter=types.INGESTION_COMPLETED,
        enabled=True,
        cooldown_seconds=0,
    )
    db.add(trigger)
    await db.flush()
    event, _ = await store.record_event(
        db,
        tenant_id=two_tenants["tenant_a"],
        event_type=types.INGESTION_COMPLETED,
        payload={},
    )

    await dispatcher.dispatch_once(db, batch=10)

    assert event.status == "ignored"
    assert event.error == "no system user for tenant"
