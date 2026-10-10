# tests/security/test_tenant_isolation.py
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_user_a_cannot_see_tenant_b(client: AsyncClient, two_tenants: dict) -> None:
    await client.post(
        "/api/v1/auth/login",
        json={"email": two_tenants["email_a"], "password": two_tenants["password"]},
    )
    r = await client.get("/api/v1/auth/me")
    assert r.status_code == 200
    assert r.json()["tenant_id"] == str(two_tenants["tenant_a"])
    assert r.json()["tenant_id"] != str(two_tenants["tenant_b"])