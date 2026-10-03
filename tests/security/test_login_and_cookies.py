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
        data={
            "tenant_slug": two_tenants["tenant_slug_a"],
            "email": two_tenants["email_a"],
            "password": two_tenants["password"],
        },
    )
    assert r.status_code == 303


@pytest.mark.asyncio
async def test_ambiguous_email_requires_tenant_slug(
    client: AsyncClient, two_tenants: dict
) -> None:
    from app.core.security import hash_password
    from app.db.models.user import User
    from app.db.session import SessionLocal

    async with SessionLocal() as db:
        db.add(
            User(
                tenant_id=two_tenants["tenant_b"],
                email=two_tenants["email_a"],
                password_hash=hash_password(two_tenants["password"]),
                is_active=True,
            )
        )
        await db.commit()

    ambiguous = await client.post(
        "/api/v1/auth/login",
        json={
            "email": two_tenants["email_a"],
            "password": two_tenants["password"],
        },
    )
    assert ambiguous.status_code == 401

    tenant_a_login = await client.post(
        "/api/v1/auth/login",
        json={
            "tenant_slug": two_tenants["tenant_slug_a"],
            "email": two_tenants["email_a"],
            "password": two_tenants["password"],
        },
    )
    assert tenant_a_login.status_code == 200

    tenant_b_login = await client.post(
        "/api/v1/auth/login",
        json={
            "tenant_slug": two_tenants["tenant_slug_b"],
            "email": two_tenants["email_a"],
            "password": two_tenants["password"],
        },
    )
    assert tenant_b_login.status_code == 200
    assert (await client.get("/api/v1/auth/me")).json()["tenant_id"] == str(
        two_tenants["tenant_b"]
    )


@pytest.mark.asyncio
async def test_web_logout_requires_csrf(client: AsyncClient, two_tenants: dict) -> None:
    await client.post(
        "/api/v1/auth/login",
        json={"email": two_tenants["email_a"], "password": two_tenants["password"]},
    )

    rejected = await client.post("/logout")
    assert rejected.status_code == 403

    csrf_token = client.cookies.get("sansa_csrf")
    assert csrf_token is not None
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