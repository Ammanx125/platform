# tests/integration/test_ingest_endpoint.py
import pytest
from httpx import AsyncClient


def _csrf(client: AsyncClient) -> dict[str, str]:
    t = client.cookies.get("sansa_csrf")
    return {"X-CSRF-Token": t} if t else {}


async def _login(client: AsyncClient, email: str, password: str) -> None:
    await client.post("/api/v1/auth/login", json={"email": email, "password": password})


@pytest.mark.asyncio
async def test_ingest_endpoint_rejects_file_sources(
    client: AsyncClient, two_tenants: dict
) -> None:
    await _login(client, two_tenants["email_a"], two_tenants["password"])

    r = await client.post(
        "/api/v1/datasets",
        json={"name": "csv-ish", "source_type": "csv", "config": {}},
        headers=_csrf(client),
    )
    ds_id = r.json()["id"]

    r = await client.post(
        f"/api/v1/datasets/{ds_id}/ingest",
        headers=_csrf(client),
    )
    assert r.status_code == 400
    assert "upload" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_ingest_endpoint_rejects_webhook_sources(
    client: AsyncClient, two_tenants: dict
) -> None:
    await _login(client, two_tenants["email_a"], two_tenants["password"])

    r = await client.post(
        "/api/v1/datasets/webhook",
        json={"name": "hook"},
        headers=_csrf(client),
    )
    ds_id = r.json()["id"]

    r = await client.post(
        f"/api/v1/datasets/{ds_id}/ingest",
        headers=_csrf(client),
    )
    assert r.status_code == 400
    assert "webhook" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_ingest_endpoint_enqueues_for_http(
    client: AsyncClient, two_tenants: dict
) -> None:
    """
    Registers an HTTP source with a bogus URL. We only assert that the
    endpoint returns a pending job — we do NOT run the worker here, so no
    outbound request is made.
    """
    await _login(client, two_tenants["email_a"], two_tenants["password"])

    r = await client.post(
        "/api/v1/datasets",
        json={
            "name": "http-ish",
            "source_type": "http",
            "config": {"url": "https://example.com/data.json", "format": "json"},
        },
        headers=_csrf(client),
    )
    ds_id = r.json()["id"]

    r = await client.post(
        f"/api/v1/datasets/{ds_id}/ingest",
        headers=_csrf(client),
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "pending"
    assert body["source_id"] == ds_id