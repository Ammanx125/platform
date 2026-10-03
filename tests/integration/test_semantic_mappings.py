# tests/integration/test_semantic_mappings.py
import io

import pytest
from httpx import AsyncClient

from app.db.models.dataset import DataSource, IngestionJob
from app.db.session import SessionLocal
from app.services.ingestion.service import run_job

CSV = (
    b"supplier_name,product,qty,unit_price,order_date\n"
    b"ACME,Bolt,100,0.42,2026-01-15\n"
    b"Beacon,Nut,50,0.18,2026-01-16\n"
)


def _csrf(client: AsyncClient) -> dict[str, str]:
    t = client.cookies.get("sansa_csrf")
    return {"X-CSRF-Token": t} if t else {}


async def _login(client: AsyncClient, email: str, password: str) -> None:
    await client.post("/api/v1/auth/login", json={"email": email, "password": password})


@pytest.mark.asyncio
async def test_propose_and_confirm_mappings(client: AsyncClient, two_tenants: dict) -> None:
    await _login(client, two_tenants["email_a"], two_tenants["password"])

    r = await client.post(
        "/api/v1/datasets",
        json={"name": "Semantic test", "source_type": "csv", "config": {}},
        headers=_csrf(client),
    )
    ds_id = r.json()["id"]

    r = await client.post(
        f"/api/v1/datasets/{ds_id}/upload",
        files={"file": ("data.csv", io.BytesIO(CSV), "text/csv")},
        headers=_csrf(client),
    )
    job_id = r.json()["id"]

    async with SessionLocal() as db:
        await run_job(db, job_id=job_id)

    # run the matcher
    r = await client.post(
        f"/api/v1/datasets/{ds_id}/mappings/propose",
        params={"job_id": job_id},
        headers=_csrf(client),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] >= 1

    cols_mapped = {m["source_column"] for m in body["mappings"]}
    assert "supplier_name" in cols_mapped
    assert "qty" in cols_mapped

    # list mappings
    r = await client.get(f"/api/v1/datasets/{ds_id}/mappings")
    assert r.status_code == 200
    mappings = r.json()
    assert any(m["status"] == "proposed" for m in mappings)

    # confirm one
    target = next(m for m in mappings if m["source_column"] == "supplier_name")
    r = await client.patch(
        f"/api/v1/datasets/{ds_id}/mappings/{target['id']}",
        json={"status": "confirmed"},
        headers=_csrf(client),
    )
    assert r.status_code == 200
    assert r.json()["status"] == "confirmed"

    # re-running propose does NOT recreate the confirmed mapping
    r = await client.post(
        f"/api/v1/datasets/{ds_id}/mappings/propose",
        params={"job_id": job_id},
        headers=_csrf(client),
    )
    assert r.status_code == 200
    again_cols = {m["source_column"] for m in r.json()["mappings"]}
    assert "supplier_name" not in again_cols


@pytest.mark.asyncio
async def test_propose_mappings_rejects_job_from_another_source(
    client: AsyncClient, two_tenants: dict
) -> None:
    await _login(client, two_tenants["email_a"], two_tenants["password"])

    response = await client.post(
        "/api/v1/datasets",
        json={"name": "Tenant A source", "source_type": "csv", "config": {}},
        headers=_csrf(client),
    )
    assert response.status_code == 201, response.text
    tenant_a_source_id = response.json()["id"]

    async with SessionLocal() as db:
        tenant_b_source = DataSource(
            tenant_id=two_tenants["tenant_b"],
            name="Tenant B source",
            source_type="csv",
            config={},
        )
        db.add(tenant_b_source)
        await db.flush()
        tenant_b_job = IngestionJob(
            tenant_id=two_tenants["tenant_b"],
            source_id=tenant_b_source.id,
            status="succeeded",
        )
        db.add(tenant_b_job)
        await db.commit()
        tenant_b_job_id = str(tenant_b_job.id)

    response = await client.post(
        f"/api/v1/datasets/{tenant_a_source_id}/mappings/propose",
        params={"job_id": tenant_b_job_id},
        headers=_csrf(client),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == f"job not found: {tenant_b_job_id}"