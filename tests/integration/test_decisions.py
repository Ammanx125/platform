# tests/integration/test_decisions.py
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.db.models.audit import AuditEvent
from app.db.session import SessionLocal
from app.services.audit import types as audit_types


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
    async with SessionLocal() as db:
        audit_event = (
            await db.execute(
                select(AuditEvent).where(
                    AuditEvent.subject_id == body["id"],
                    AuditEvent.event_type == audit_types.DECISION_CREATED,
                )
            )
        ).scalar_one()
        assert audit_event.actor_user_id == two_tenants["user_a"]
        assert audit_event.subject_type == "decision_run"
        assert audit_event.event_metadata["query"] == body["query"]


@pytest.mark.asyncio
async def test_ask_runs_domain_workflow_behind_business_outcome(
    client: AsyncClient, two_tenants: dict
) -> None:
    await _login(client, two_tenants["email_a"], two_tenants["password"])

    response = await client.post(
        "/ask",
        data={"query": "How are operations performing?"},
        headers=_csrf(client),
    )

    assert response.status_code == 200
    assert "Operations health" in response.text
    assert "Sansa ran downtime anomalies and capacity utilization checks" in response.text
    assert "How Sansa got here" in response.text
    assert "Operations SLA Monitoring" in response.text
    assert "/workflows/instances/" in response.text
    assert '<details class="workflow-trace">' in response.text


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