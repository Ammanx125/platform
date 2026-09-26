# tests/integration/test_decisions.py
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
async def test_run_decision_with_mock_llm(
    client: AsyncClient, two_tenants: dict
) -> None:
    await _login(client, two_tenants["email_a"], two_tenants["password"])

    r = await client.post(
        "/api/v1/decisions",
        json={"query": "Why did procurement cost increase?"},
        headers=_csrf(client),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["query"] == "Why did procurement cost increase?"
    assert body["llm_provider"] == "mock"
    assert body["llm_summary"] == "Mock response"
    assert body["finished_at"] is not None


@pytest.mark.asyncio
async def test_list_and_get_decision(
    client: AsyncClient, two_tenants: dict
) -> None:
    await _login(client, two_tenants["email_a"], two_tenants["password"])
    r = await client.post(
        "/api/v1/decisions",
        json={"query": "Explain supplier spend."},
        headers=_csrf(client),
    )
    decision_id = r.json()["id"]

    r = await client.get("/api/v1/decisions")
    assert r.status_code == 200
    assert any(d["id"] == decision_id for d in r.json())

    r = await client.get(f"/api/v1/decisions/{decision_id}")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_tenant_isolation(client: AsyncClient, two_tenants: dict) -> None:
    await _login(client, two_tenants["email_a"], two_tenants["password"])
    r = await client.post(
        "/api/v1/decisions",
        json={"query": "A-only query"},
        headers=_csrf(client),
    )
    decision_id = r.json()["id"]

    client.cookies.clear()
    await _login(client, two_tenants["email_b"], two_tenants["password"])
    r = await client.get(f"/api/v1/decisions/{decision_id}")
    assert r.status_code == 404