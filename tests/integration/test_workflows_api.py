# tests/integration/test_workflows_api.py
import pytest
from httpx import AsyncClient


def _csrf(client: AsyncClient) -> dict[str, str]:
    t = client.cookies.get("sansa_csrf")
    return {"X-CSRF-Token": t} if t else {}


async def _login(client: AsyncClient, email: str, password: str) -> None:
    await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )


@pytest.mark.asyncio
async def test_list_workflow_definitions(client: AsyncClient, two_tenants: dict) -> None:
    await _login(client, two_tenants["email_a"], two_tenants["password"])
    r = await client.get("/api/v1/workflows/definitions")
    assert r.status_code == 200
    keys = {w["key"] for w in r.json()}
    assert "procurement.spend_analysis" in keys


@pytest.mark.asyncio
async def test_start_spend_analysis(
    client: AsyncClient, two_tenants: dict
) -> None:
    await _login(client, two_tenants["email_a"], two_tenants["password"])
    r = await client.post(
        "/api/v1/workflows/definitions/procurement.spend_analysis/start",
        json={"source_ids": None},
        headers=_csrf(client),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["workflow_key"] == "procurement.spend_analysis"
    assert body["status"] in ("completed", "failed", "waiting_workflow_approval")
    # With no data, it should still complete (empty evidence).
    assert body["status"] == "completed"


@pytest.mark.asyncio
async def test_tenant_isolation(
    client: AsyncClient, two_tenants: dict
) -> None:
    await _login(client, two_tenants["email_a"], two_tenants["password"])
    r = await client.post(
        "/api/v1/workflows/definitions/inventory.stock_analysis/start",
        json={},
        headers=_csrf(client),
    )
    instance_id = r.json()["id"]

    client.cookies.clear()
    await _login(client, two_tenants["email_b"], two_tenants["password"])
    r = await client.get(f"/api/v1/workflows/instances/{instance_id}")
    assert r.status_code == 404