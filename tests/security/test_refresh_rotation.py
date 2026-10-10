# tests/security/test_refresh_rotation.py
import pytest
from httpx import AsyncClient


def _csrf_headers(client: AsyncClient) -> dict[str, str]:
    token = client.cookies.get("sansa_csrf")
    return {"X-CSRF-Token": token} if token else {}


@pytest.mark.asyncio
async def test_refresh_rotates(client: AsyncClient, two_tenants: dict) -> None:
    await client.post(
        "/api/v1/auth/login",
        json={"email": two_tenants["email_a"], "password": two_tenants["password"]},
    )
    old_refresh = client.cookies.get("sansa_refresh")

    r = await client.post("/api/v1/auth/refresh", headers=_csrf_headers(client))
    assert r.status_code == 200
    new_refresh = client.cookies.get("sansa_refresh")
    assert new_refresh and new_refresh != old_refresh


@pytest.mark.asyncio
async def test_reuse_revokes_family(client: AsyncClient, two_tenants: dict) -> None:
    await client.post(
        "/api/v1/auth/login",
        json={"email": two_tenants["email_a"], "password": two_tenants["password"]},
    )
    original = client.cookies.get("sansa_refresh")

    # Legitimate rotation
    r1 = await client.post("/api/v1/auth/refresh", headers=_csrf_headers(client))
    assert r1.status_code == 200

    # Replay the ORIGINAL (revoked) token
    client.cookies.set("sansa_refresh", original)
    r2 = await client.post("/api/v1/auth/refresh", headers=_csrf_headers(client))
    assert r2.status_code == 401

    # The rotated-to token should also now be dead
    rotated = r1.cookies.get("sansa_refresh")
    client.cookies.set("sansa_refresh", rotated)
    r3 = await client.post("/api/v1/auth/refresh", headers=_csrf_headers(client))
    assert r3.status_code == 401