# tests/integration/test_actions_approval.py
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from app.db.models.action import ActionRecord
from app.db.session import SessionLocal


def _csrf(client: AsyncClient) -> dict[str, str]:
    t = client.cookies.get("sansa_csrf")
    return {"X-CSRF-Token": t} if t else {}


async def _login(client: AsyncClient, email: str, password: str) -> None:
    await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )


async def _seed_pending_action(tenant_id, user_id, tool="generate_report") -> str:
    async with SessionLocal() as db:
        record = ActionRecord(
            tenant_id=tenant_id,
            user_id=user_id,
            tool_name=tool,
            arguments={"title": "pending", "body": "x" * 50},
            status="pending_approval",
            proposed_at=datetime.now(UTC),
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)
        return str(record.id)


@pytest.mark.asyncio
async def test_approve_own_action_is_rejected(
    client: AsyncClient, two_tenants: dict
) -> None:
    await _login(client, two_tenants["email_a"], two_tenants["password"])
    action_id = await _seed_pending_action(
        two_tenants["tenant_a"], two_tenants["user_a"]
    )

    r = await client.post(
        f"/api/v1/actions/{action_id}/approve",
        headers=_csrf(client),
    )
    assert r.status_code == 409
    assert "cannot approve their own action" in r.json()["detail"]


@pytest.mark.asyncio
async def test_reject_own_action_is_rejected(
    client: AsyncClient, two_tenants: dict
) -> None:
    await _login(client, two_tenants["email_a"], two_tenants["password"])
    action_id = await _seed_pending_action(
        two_tenants["tenant_a"], two_tenants["user_a"]
    )

    r = await client.post(
        f"/api/v1/actions/{action_id}/reject",
        json={"reason": "no"},
        headers=_csrf(client),
    )
    assert r.status_code == 409
    assert "cannot reject their own action" in r.json()["detail"]