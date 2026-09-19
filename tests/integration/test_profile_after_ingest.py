# tests/integration/test_profile_after_ingest.py
import io

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.db.models.dataset import IngestionJob
from app.db.models.understanding import DataProfile
from app.db.session import SessionLocal
from app.services.ingestion.service import run_job


CSV = (
    b"supplier,supplier_id,quantity,price,order_date\n"
    b"ACME,1,100,0.42,2026-01-15\n"
    b"ACME,1,200,0.40,2026-01-16\n"
    b"Beacon,2,50,18.75,2026-01-20\n"
)


def _csrf(client: AsyncClient) -> dict[str, str]:
    t = client.cookies.get("sansa_csrf")
    return {"X-CSRF-Token": t} if t else {}


async def _login(client: AsyncClient, email: str, password: str) -> None:
    await client.post("/api/v1/auth/login", json={"email": email, "password": password})


@pytest.mark.asyncio
async def test_profile_is_written_after_ingest(client: AsyncClient, two_tenants: dict) -> None:
    await _login(client, two_tenants["email_a"], two_tenants["password"])

    r = await client.post(
        "/api/v1/datasets",
        json={"name": "Profile test", "source_type": "csv", "config": {}},
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

    async with SessionLocal() as db:
        job = (await db.execute(select(IngestionJob).where(IngestionJob.id == job_id))).scalar_one()
        assert job.status == "succeeded"

        profile = (await db.execute(select(DataProfile).where(DataProfile.job_id == job_id))).scalar_one()
        assert profile.row_count == 3
        assert profile.column_count == 5

        # supplier_id has a duplicate, so duplicate_entity should fire
        issue_codes = {i["code"] for i in profile.issues}
        assert "duplicate_entity" in issue_codes


@pytest.mark.asyncio
async def test_profile_endpoints(client: AsyncClient, two_tenants: dict) -> None:
    await _login(client, two_tenants["email_a"], two_tenants["password"])

    r = await client.post(
        "/api/v1/datasets",
        json={"name": "Profile endpoints", "source_type": "csv", "config": {}},
        headers=_csrf(client),
    )
    ds_id = r.json()["id"]

    r = await client.post(
        f"/api/v1/datasets/{ds_id}/upload",
        files={"file": ("data.csv", io.BytesIO(CSV), "text/csv")},
        headers=_csrf(client),
    )
    job_id = r.json()["id"]

    # Before run_job, GET should 404
    r = await client.get(f"/api/v1/datasets/{ds_id}/jobs/{job_id}/profile")
    assert r.status_code == 404

    async with SessionLocal() as db:
        await run_job(db, job_id=job_id)

    r = await client.get(f"/api/v1/datasets/{ds_id}/jobs/{job_id}/profile")
    assert r.status_code == 200
    body = r.json()
    assert body["row_count"] == 3
    assert body["column_count"] == 5

    # Recompute works
    r = await client.post(
        f"/api/v1/datasets/{ds_id}/jobs/{job_id}/profile",
        headers=_csrf(client),
    )
    assert r.status_code == 200
    assert r.json()["row_count"] == 3