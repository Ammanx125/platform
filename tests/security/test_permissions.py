# tests/security/test_permissions.py
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_admin_has_user_manage(client: AsyncClient, two_tenants: dict) -> None:
    await client.post(
        "/api/v1/auth/login",
        json={"email": two_tenants["email_a"], "password": two_tenants["password"]},
    )
    r = await client.get("/api/v1/auth/me")
    body = r.json()
    assert "admin" in body["roles"]
    assert "user:manage" in body["permissions"]
    assert "action:approve" in body["permissions"]