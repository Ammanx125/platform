# tests/security/test_login_and_cookies.py
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_login_sets_cookies(client: AsyncClient, two_tenants: dict) -> None:
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": two_tenants["email_a"], "password": two_tenants["password"]},
    )
    assert r.status_code == 200
    assert "sansa_access" in r.cookies
    assert "sansa_refresh" in r.cookies
    assert "sansa_csrf" in r.cookies


@pytest.mark.asyncio
async def test_login_rejects_bad_password(client: AsyncClient, two_tenants: dict) -> None:
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": two_tenants["email_a"], "password": "wrongpass1"},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_web_login_is_csrf_exempt(client: AsyncClient, two_tenants: dict) -> None:
    r = await client.post(
        "/login",
        data={"email": two_tenants["email_a"], "password": two_tenants["password"]},
    )
    assert r.status_code == 303


@pytest.mark.asyncio
async def test_web_logout_requires_csrf(client: AsyncClient, two_tenants: dict) -> None:
    await client.post(
        "/api/v1/auth/login",
        json={"email": two_tenants["email_a"], "password": two_tenants["password"]},
    )

    rejected = await client.post("/logout")
    assert rejected.status_code == 403

    csrf_token = client.cookies.get("sansa_csrf")
    accepted = await client.post(
        "/logout", headers={"X-CSRF-Token": csrf_token}
    )
    assert accepted.status_code == 303
    assert accepted.headers["location"] == "/login"


@pytest.mark.asyncio
async def test_me_requires_auth(client: AsyncClient) -> None:
    r = await client.get("/api/v1/auth/me")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_me_returns_own_tenant(client: AsyncClient, two_tenants: dict) -> None:
    await client.post(
        "/api/v1/auth/login",
        json={"email": two_tenants["email_a"], "password": two_tenants["password"]},
    )
    r = await client.get("/api/v1/auth/me")
    assert r.status_code == 200
    body = r.json()
    assert body["tenant_id"] == str(two_tenants["tenant_a"])
    assert body["email"] == two_tenants["email_a"]