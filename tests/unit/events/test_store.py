# tests/unit/events/test_store.py
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionLocal
from app.services.events import store as events_store
from app.services.events import types as event_types


@pytest_asyncio.fixture
async def db(two_tenants: dict) -> AsyncGenerator[AsyncSession]:
    _ = two_tenants
    async with SessionLocal() as session:
        yield session


@pytest.mark.asyncio
async def test_record_and_dedup(db, two_tenants):
    e1, created1 = await events_store.record_event(
        db,
        tenant_id=two_tenants["tenant_a"],
        event_type=event_types.FILE_OBSERVED,
        user_id=two_tenants["user_a"],
        actor_kind="user",
        payload={"path": "a.csv"},
        dedup_key="test:1",
    )
    assert created1 is True
    assert e1.user_id == two_tenants["user_a"]
    assert e1.actor_kind == "user"

    e2, created2 = await events_store.record_event(
        db,
        tenant_id=two_tenants["tenant_a"],
        event_type=event_types.FILE_OBSERVED,
        payload={"path": "a.csv"},
        dedup_key="test:1",
    )
    assert created2 is False
    assert e2.id == e1.id


@pytest.mark.asyncio
async def test_payload_redacted(db, two_tenants):
    event, _ = await events_store.record_event(
        db,
        tenant_id=two_tenants["tenant_a"],
        event_type=event_types.FILE_OBSERVED,
        payload={"auth_header": "Bearer abc123def456"},
    )
    assert "abc123def456" not in str(event.payload)
    assert event.actor_kind == "system"


@pytest.mark.asyncio
async def test_actor_kind_validation_and_filter(db, two_tenants):
    with pytest.raises(ValueError, match="actor_kind must be one of"):
        await events_store.record_event(
            db,
            tenant_id=two_tenants["tenant_a"],
            event_type=event_types.FILE_OBSERVED,
            actor_kind="robot",
        )

    user_event, _ = await events_store.record_event(
        db,
        tenant_id=two_tenants["tenant_a"],
        event_type=event_types.FILE_OBSERVED,
        user_id=two_tenants["user_a"],
        actor_kind="user",
    )
    service_event, _ = await events_store.record_event(
        db,
        tenant_id=two_tenants["tenant_a"],
        event_type=event_types.WEBHOOK_RECEIVED,
        actor_kind="service",
    )

    user_events = await events_store.list_events(
        db, tenant_id=two_tenants["tenant_a"], actor_kind="user"
    )
    service_events = await events_store.list_events(
        db, tenant_id=two_tenants["tenant_a"], actor_kind="service"
    )
    assert user_event in user_events
    assert service_event not in user_events
    assert service_event in service_events