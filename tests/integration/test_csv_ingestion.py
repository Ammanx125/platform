# tests/integration/test_csv_ingestion.py
import io
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.db.models.audit import AuditEvent
from app.db.models.dataset import DataSource, IngestionJob, StagedRow
from app.db.session import SessionLocal
from app.services.audit import types as audit_types
from app.services.ingestion.service import run_job
from app.services.storage.local import storage

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
        audit_event = (
            await db.execute(
                select(AuditEvent).where(
                    AuditEvent.subject_id == job.id,
                    AuditEvent.event_type == audit_types.INGESTION_COMPLETED,
                )
            )
        ).scalar_one()
        assert audit_event.subject_type == "ingestion_job"
        assert audit_event.event_metadata == {
            "source_id": ds_id,
            "source_type": "csv",
            "rows_read": 2,
            "rows_staged": 2,
        }

        staged = (await db.execute(select(StagedRow).where(StagedRow.job_id == job_id))).scalars().all()
        assert len(staged) == 2
        assert staged[0].raw_data["supplier"] in ("Acme", "Beacon")


@pytest.mark.asyncio
async def test_agent_csv_ingestion_uses_job_storage_key(
    two_tenants: dict,
) -> None:
    tenant_id = two_tenants["tenant_a"]
    storage_key = f"{tenant_id}/{uuid.uuid4()}.csv"
    incorrect_source_key = f"{tenant_id}/{uuid.uuid4()}.csv"

    async with SessionLocal() as db:
        source = DataSource(
            tenant_id=tenant_id,
            name=f"Agent CSV {uuid.uuid4()}",
            source_type="agent",
            config={"storage_key": incorrect_source_key},
        )
        db.add(source)
        await db.flush()

        job = IngestionJob(
            tenant_id=tenant_id,
            source_id=source.id,
            storage_key=storage_key,
            pending_path="reports/agent.csv",
            pending_hash="a" * 64,
        )
        db.add(job)
        await db.commit()
        job_id = job.id

    await storage.put(key=storage_key, content=CSV)
    try:
        async with SessionLocal() as db:
            await run_job(db, job_id=job_id)

        async with SessionLocal() as db:
            job = (
                await db.execute(
                    select(IngestionJob).where(IngestionJob.id == job_id)
                )
            ).scalar_one()
            assert job.status == "succeeded"
            staged = (
                await db.execute(
                    select(StagedRow).where(StagedRow.job_id == job_id)
                )
            ).scalars().all()
            assert len(staged) == 2
            assert {row.raw_data["supplier"] for row in staged} == {
                "Acme",
                "Beacon",
            }
    finally:
        await storage.delete(key=storage_key)