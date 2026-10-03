# tests/integration/test_agents_api.py
"""
Agent enrollment, registration, heartbeat, and sync.
"""
from __future__ import annotations

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
async def test_enroll_register_sync_flow(
    client: AsyncClient, two_tenants: dict
) -> None:
    await _login(client, two_tenants["email_a"], two_tenants["password"])

    # Create a source of type 'agent'.
    r = await client.post(
        "/api/v1/datasets",
        json={"name": "Agent source", "source_type": "agent", "config": {}},
        headers=_csrf(client),
    )
    assert r.status_code == 201, r.text
    source_id = r.json()["id"]

    # Enroll.
    r = await client.post(
        "/api/v1/agents/enroll",
        json={
            "name": "Test Agent",
            "description": "test",
            "source_id": source_id,
        },
        headers=_csrf(client),
    )
    assert r.status_code == 201, r.text
    enroll = r.json()
    agent_id = enroll["agent_id"]
    enrollment_token = enroll["enrollment_token"]

    # Register (agent-side, unauthenticated).
    r = await client.post(
        "/api/v1/agents/register",
        json={
            "enrollment_token": enrollment_token,
            "agent_metadata": {"hostname": "test-host"},
        },
    )
    assert r.status_code == 200, r.text
    register = r.json()
    credential = register["credential"]
    assert register["agent_id"] == agent_id
    assert register["source_id"] == source_id

    auth = {"Authorization": f"Bearer {credential}"}

    # Heartbeat.
    r = await client.post(
        f"/api/v1/agents/{agent_id}/heartbeat",
        json={"agent_metadata": {"version": "0.1.0"}},
        headers=auth,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active"

    # Sync a batch.
    r = await client.post(
        f"/api/v1/agents/{agent_id}/sync",
        json={
            "files": [
                {
                    "path": "reports/q3.csv",
                    "content_hash": "a" * 64,
                    "byte_size": 1024,
                    "mtime": "2026-01-15T10:00:00+00:00",
                    "ctime": "2026-01-15T10:00:00+00:00",
                    "status": "new",
                },
                {
                    "path": "reports/q4.csv",
                    "content_hash": "b" * 64,
                    "byte_size": 2048,
                    "mtime": "2026-01-20T10:00:00+00:00",
                    "ctime": "2026-01-20T10:00:00+00:00",
                    "status": "new",
                },
            ]
        },
        headers=auth,
    )
    assert r.status_code == 200, r.text
    assert r.json()["counts"]["new"] == 2

    # Re-sync the same batch: no new rows.
    r = await client.post(
        f"/api/v1/agents/{agent_id}/sync",
        json={
            "files": [
                {
                    "path": "reports/q3.csv",
                    "content_hash": "a" * 64,
                    "byte_size": 1024,
                    "status": "unchanged",
                },
            ]
        },
        headers=auth,
    )
    assert r.status_code == 200
    assert r.json()["counts"]["unchanged"] == 1


@pytest.mark.asyncio
async def test_agent_cannot_push_to_other_tenant(
    client: AsyncClient, two_tenants: dict
) -> None:
    await _login(client, two_tenants["email_a"], two_tenants["password"])

    r = await client.post(
        "/api/v1/datasets",
        json={"name": "A agent src", "source_type": "agent", "config": {}},
        headers=_csrf(client),
    )
    source_id = r.json()["id"]

    r = await client.post(
        "/api/v1/agents/enroll",
        json={"name": "A Agent", "source_id": source_id},
        headers=_csrf(client),
    )
    token = r.json()["enrollment_token"]

    r = await client.post(
        "/api/v1/agents/register",
        json={"enrollment_token": token, "agent_metadata": {}},
    )
    credential = r.json()["credential"]

    # Try to sync with a different agent id.
    import uuid
    other_id = uuid.uuid4()
    r = await client.post(
        f"/api/v1/agents/{other_id}/sync",
        json={"files": []},
        headers={"Authorization": f"Bearer {credential}"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_enroll_requires_agent_source(
    client: AsyncClient, two_tenants: dict
) -> None:
    await _login(client, two_tenants["email_a"], two_tenants["password"])

    r = await client.post(
        "/api/v1/datasets",
        json={"name": "CSV source", "source_type": "csv", "config": {}},
        headers=_csrf(client),
    )
    source_id = r.json()["id"]

    r = await client.post(
        "/api/v1/agents/enroll",
        json={"name": "wrong type", "source_id": source_id},
        headers=_csrf(client),
    )
    assert r.status_code == 400
    assert "agent" in r.json()["detail"].lower()