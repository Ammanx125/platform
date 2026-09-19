# tests/integration/test_csv_ingestion.py
import io

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.db.models.dataset import IngestionJob, StagedRow
from app.db.session import SessionLocal
from app.services.ingestion.service import run_job

CSV = b"supplier,product,qty,price\nAcme,Bolt,100,0.42\nBeacon,Nut,200,0.18\n"


def _csrf(client: AsyncClient) -> dict[str, str]:
    t = client.cookies.get("sansa_csrf")
    return {"X-CSRF-Token": t} if t else {}


async def _login(client: AsyncClient, email: str, password: str) -> None:
    await client.post("/api/v1/auth/login", json={"email": email, "password": password})


@pytest.mark.asyncio
async def test_csv_upload_and_run(client: AsyncClient, two_tenants: dict) -> None:
    await _login(client, two_tenants["email_a"], two_tenants["password"])

    r = await client.post(
        "/api/v1/datasets",
        json={"name": "CSV test", "source_type": "csv", "config": {}},
        headers=_csrf(client),
    )
    ds_id = r.json()["id"]

    r = await client.post(
        f"/api/v1/datasets/{ds_id}/upload",
        files={"file": ("data.csv", io.BytesIO(CSV), "text/csv")},
        headers=_csrf(client),
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["id"]

    # Run the worker step inline
    async with SessionLocal() as db:
        await run_job(db, job_id=job_id)

    async with SessionLocal() as db:
        job = (await db.execute(select(IngestionJob).where(IngestionJob.id == job_id))).scalar_one()
        assert job.status == "succeeded"
        assert job.rows_read == 2
        assert job.rows_staged == 2

        staged = (await db.execute(select(StagedRow).where(StagedRow.job_id == job_id))).scalars().all()
        assert len(staged) == 2
        assert staged[0].raw_data["supplier"] in ("Acme", "Beacon")