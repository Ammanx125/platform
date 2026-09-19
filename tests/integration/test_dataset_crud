# tests/integration/test_dataset_crud.py
import pytest
from httpx import AsyncClient


def _csrf(client: AsyncClient) -> dict[str, str]:
    t = client.cookies.get("sansa_csrf")
    return {"X-CSRF-Token": t} if t else {}


async def _login(client: AsyncClient, email: str, password: str) -> None:
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_create_and_list_dataset(client: AsyncClient, two_tenants: dict) -> None:
    await _login(client, two_tenants["email_a"], two_tenants["password"])

    r = await client.post(
        "/api/v1/datasets",
        json={"name": "Test Source", "source_type": "csv", "config": {}},
        headers=_csrf(client),
    )
    assert r.status_code == 201, r.text
    ds_id = r.json()["id"]

    r = await client.get("/api/v1/datasets")
    assert r.status_code == 200
    ids = [d["id"] for d in r.json()]
    assert ds_id in ids


@pytest.mark.asyncio
async def test_get_other_tenant_dataset_returns_404(
    client: AsyncClient, two_tenants: dict
) -> None:
    # Tenant A creates a dataset
    await _login(client, two_tenants["email_a"], two_tenants["password"])
    r = await client.post(
        "/api/v1/datasets",
        json={"name": "A-only", "source_type": "csv", "config": {}},
        headers=_csrf(client),
    )
    a_id = r.json()["id"]

    # switch to Tenant B
    client.cookies.clear()
    await _login(client, two_tenants["email_b"], two_tenants["password"])

    r = await client.get(f"/api/v1/datasets/{a_id}")
    assert r.status_code == 404