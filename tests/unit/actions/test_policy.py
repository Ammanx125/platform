# tests/unit/actions/test_policy.py
import uuid
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.actions import policy as policy_service
from app.services.actions.base import RISK_READ, RISK_REQUIRED


class _FakeResult:
    def __init__(self, row):
        self._row = row
    def scalar_one_or_none(self):
        return self._row


class _FakeDB:
    def __init__(self, row=None):
        self._row = row
    async def execute(self, *a, **k):
        return _FakeResult(self._row)


@pytest.mark.asyncio
async def test_default_for_read_is_auto():
    db = cast(AsyncSession, _FakeDB(row=None))
    result = await policy_service.resolve(
        db, tenant_id=uuid.uuid4(), tool_name="list_suppliers",
        tool_risk_level=RISK_READ,
    )
    assert result == policy_service.POLICY_AUTO


@pytest.mark.asyncio
async def test_default_for_required_is_require_approval():
    db = cast(AsyncSession, _FakeDB(row=None))
    result = await policy_service.resolve(
        db, tenant_id=uuid.uuid4(), tool_name="create_po",
        tool_risk_level=RISK_REQUIRED,
    )
    assert result == policy_service.POLICY_REQUIRE_APPROVAL


@pytest.mark.asyncio
async def test_override_wins():
    class _Row:
        policy_override = policy_service.POLICY_AUTO
    db = cast(AsyncSession, _FakeDB(row=_Row()))
    result = await policy_service.resolve(
        db, tenant_id=uuid.uuid4(), tool_name="create_po",
        tool_risk_level=RISK_REQUIRED,
    )
    assert result == policy_service.POLICY_REQUIRE_APPROVAL


@pytest.mark.asyncio
async def test_required_tool_cannot_override_to_auto(monkeypatch):
    class _Db:
        async def execute(self, *args, **kwargs):
            return _FakeResult(None)

        def add(self, row):
            return None

        async def flush(self):
            return None

    monkeypatch.setattr(
        policy_service,
        "get_action",
        lambda name: type("Tool", (), {"risk_level": "required"})(),
    )

    with pytest.raises(ValueError, match="auto execution is not allowed"):
        await policy_service.set_override(
            cast(AsyncSession, _Db()),
            tenant_id=uuid.uuid4(),
            tool_name="create_po",
            policy_override=policy_service.POLICY_AUTO,
            actor_user_id=uuid.uuid4(),
        )