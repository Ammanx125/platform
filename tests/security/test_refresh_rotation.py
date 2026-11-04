# tests/security/test_refresh_rotation.py
import pytest
from httpx import AsyncClient

from app.core.config import settings


def _csrf_headers(client: AsyncClient) -> dict[str, str]:
    token = client.cookies.get(settings.csrf_cookie_name)
    return {settings.csrf_header_name: token} if token else {}


@pytest.mark.asyncio
async def test_refresh_rotates(client: AsyncClient, two_tenants: dict) -> None:
    await client.post(
        "/api/v1/auth/login",
        json={"email": two_tenants["email_a"], "password": two_tenants["password"]},
    )
    old_refresh = client.cookies.get(settings.refresh_cookie_name)

    r = await client.post("/api/v1/auth/refresh", headers=_csrf_headers(client))
    assert r.status_code == 200
    new_refresh = client.cookies.get(settings.refresh_cookie_name)
    assert new_refresh and new_refresh != old_refresh


@pytest.mark.asyncio
async def test_reuse_revokes_family(client: AsyncClient, two_tenants: dict) -> None:
    await client.post(
        "/api/v1/auth/login",
        json={"email": two_tenants["email_a"], "password": two_tenants["password"]},
    )
    original = client.cookies.get(settings.refresh_cookie_name)

    # Legitimate rotation
    r1 = await client.post("/api/v1/auth/refresh", headers=_csrf_headers(client))
    assert r1.status_code == 200

    # Replay the ORIGINAL (revoked) token
    client.cookies.set(settings.refresh_cookie_name, original)
    r2 = await client.post("/api/v1/auth/refresh", headers=_csrf_headers(client))
    assert r2.status_code == 401

    # The rotated-to token should also now be dead
    rotated = r1.cookies.get(settings.refresh_cookie_name)
    client.cookies.set(settings.refresh_cookie_name, rotated)
    r3 = await client.post("/api/v1/auth/refresh", headers=_csrf_headers(client))
    assert r3.status_code == 401