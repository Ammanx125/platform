import pytest
from httpx import AsyncClient

from app.db.session import SessionLocal
from app.services.audit import service as audit_service
from app.services.audit import types as audit_types


def _csrf(client: AsyncClient) -> dict[str, str]:
    token = client.cookies.get("sansa_csrf")
    return {"X-CSRF-Token": token} if token else {}


async def _login(client: AsyncClient, email: str, password: str) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_list_audit_events_is_tenant_scoped(
    client: AsyncClient, two_tenants: dict
) -> None:
    await _login(client, two_tenants["email_a"], two_tenants["password"])

    async with SessionLocal() as db:
        event = await audit_service.emit(
            db,
            tenant_id=two_tenants["tenant_a"],
            actor_user_id=two_tenants["user_a"],
            event_type=audit_types.DECISION_CREATED,
            subject_type="decision_run",
            metadata={"query": "procurement spend"},
            message="Decision created",
        )
        await db.commit()
        event_id = str(event.id)

    response = await client.get("/api/v1/audit", params={"limit": 200})

    assert response.status_code == 200, response.text
    body = response.json()
    result = next(item for item in body if item["id"] == event_id)
    assert result["event_type"] == audit_types.DECISION_CREATED
    assert result["message"] == "Decision created"
    assert result["event_metadata"] == {"query": "procurement spend"}

    client.cookies.clear()
    await _login(client, two_tenants["email_b"], two_tenants["password"])
    response = await client.get("/api/v1/audit", params={"limit": 200})

    assert response.status_code == 200, response.text
    assert all(item["id"] != event_id for item in response.json())
