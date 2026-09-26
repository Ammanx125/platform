# tests/unit/events/test_store.py
import pytest

from app.services.events import store as events_store
from app.services.events import types as event_types


@pytest.mark.asyncio
async def test_record_and_dedup(db, two_tenants):
    e1, created1 = await events_store.record_event(
        db,
        tenant_id=two_tenants["tenant_a"],
        event_type=event_types.FILE_OBSERVED,
        payload={"path": "a.csv"},
        dedup_key="test:1",
    )
    assert created1 is True

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