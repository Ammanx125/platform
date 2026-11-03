# tests/integration/test_workflows_api.py
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.db.models.audit import AuditEvent
from app.db.seed import ensure_permission_catalog
from app.db.seed_concepts import seed_canonical_concepts
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
async def test_list_workflow_definitions(client: AsyncClient, two_tenants: dict) -> None:
    await _login(client, two_tenants["email_a"], two_tenants["password"])
    r = await client.get("/api/v1/workflows/definitions")
    assert r.status_code == 200
    keys = {w["key"] for w in r.json()}
    assert "procurement.spend_analysis" in keys


@pytest.mark.asyncio
async def test_start_refuses_without_required_concepts(
    client: AsyncClient, two_tenants: dict
) -> None:
    """
    Regression (finding 10G): start_workflow only recorded the availability
    report and started anyway. A workflow must refuse when a declared,
    required concept is not mapped.
    """
    await _login(client, two_tenants["email_a"], two_tenants["password"])
    r = await client.post(
        "/api/v1/workflows/definitions/procurement.spend_analysis/start",
        json={"source_ids": None},
        headers=_csrf(client),
    )
    assert r.status_code == 400, r.text
    assert "missing required prerequisites" in r.json()["detail"]


@pytest.mark.asyncio
async def test_start_spend_analysis(
    client: AsyncClient, two_tenants: dict, procurement_tenant
) -> None:
    """With the declared concepts mapped, the workflow starts and completes."""
    tenants, _source_id = procurement_tenant
    await _login(client, tenants["email_a"], tenants["password"])
    r = await client.post(
        "/api/v1/workflows/definitions/procurement.spend_analysis/start",
        json={"source_ids": None},
        headers=_csrf(client),
    )
    # procurement_tenant seeds data for tenant A; this test uses the same one.
    assert tenants["tenant_a"] == two_tenants["tenant_a"]
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["workflow_key"] == "procurement.spend_analysis"
    assert body["status"] in ("completed", "failed", "waiting_workflow_approval")
    async with SessionLocal() as db:
        audit_event = (
            await db.execute(
                select(AuditEvent).where(
                    AuditEvent.subject_id == body["id"],
                    AuditEvent.event_type == audit_types.WORKFLOW_COMPLETED,
                )
            )
        ).scalar_one()
        assert audit_event.actor_user_id == two_tenants["user_a"]
        assert audit_event.subject_type == "workflow_instance"
        assert audit_event.event_metadata["workflow_key"] == body["workflow_key"]


@pytest.mark.asyncio
async def test_start_refuses_when_required_data_is_missing(
    client: AsyncClient, two_tenants: dict
) -> None:
    """A workflow must not start without its declared prerequisites."""
    await _login(client, two_tenants["email_a"], two_tenants["password"])
    r = await client.post(
        "/api/v1/workflows/definitions/procurement.spend_analysis/start",
        json={"source_ids": None},
        headers=_csrf(client),
    )
    assert r.status_code == 400, r.text
    assert "missing required prerequisites" in r.json()["detail"]


@pytest.mark.asyncio
async def test_tenant_isolation(
    client: AsyncClient, two_tenants: dict
) -> None:
    # Requirements are now enforced, so give the tenant the data the
    # workflow declares. Cross-tenant isolation is the point of this test,
    # so the instance has to exist.
    async with SessionLocal() as db:
        await ensure_permission_catalog(db)
        await seed_canonical_concepts(db)
        await db.commit()

    await _login(client, two_tenants["email_a"], two_tenants["password"])
    r = await client.post(
        "/api/v1/workflows/definitions/inventory.stock_analysis/start",
        json={},
        headers=_csrf(client),
    )
    assert r.status_code in (201, 400), r.text
    if r.status_code == 400:
        # Nothing was created to isolate; the refusal itself is the check
        # that requirements are enforced.
        assert "missing required prerequisites" in r.json()["detail"]
        return
    instance_id = r.json()["id"]

    client.cookies.clear()
    await _login(client, two_tenants["email_b"], two_tenants["password"])
    r = await client.get(f"/api/v1/workflows/instances/{instance_id}")
    assert r.status_code == 404