# tests/integration/test_file_observation.py
import io

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.db.models.timestamps import FileObservation, RowTimestamp
from app.db.session import SessionLocal
from app.services.ingestion.service import hash_bytes, run_job

CSV = b"date,value\n2026-01-01,10\n2026-01-02,20\n"


def _csrf(client: AsyncClient) -> dict[str, str]:
    t = client.cookies.get("sansa_csrf")
    return {"X-CSRF-Token": t} if t else {}


async def _login(client: AsyncClient, email: str, password: str) -> None:
    await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )


@pytest.mark.asyncio
async def test_upload_creates_file_observation(
    client: AsyncClient, two_tenants: dict
) -> None:
    await _login(client, two_tenants["email_a"], two_tenants["password"])

    r = await client.post(
        "/api/v1/datasets",
        json={"name": "obs test", "source_type": "csv", "config": {}},
        headers=_csrf(client),
    )
    ds_id = r.json()["id"]

    r = await client.post(
        f"/api/v1/datasets/{ds_id}/upload",
        files={"file": ("data.csv", io.BytesIO(CSV), "text/csv")},
        headers=_csrf(client),
    )
    assert r.status_code == 202
    job_id = r.json()["id"]

    async with SessionLocal() as db:
        obs = (
            await db.execute(
                select(FileObservation).where(
                    FileObservation.source_id == ds_id,
                )
            )
        ).scalars().all()
        assert len(obs) == 1
        assert obs[0].path == "data.csv"
        assert obs[0].content_hash == hash_bytes(CSV)
        assert obs[0].status == "new"
        assert obs[0].byte_size == len(CSV)

    # Run the job; ingestion timestamps should be recorded for each row.
    async with SessionLocal() as db:
        await run_job(db, job_id=job_id)

    async with SessionLocal() as db:
        row_ts = (
            await db.execute(
                select(RowTimestamp).where(RowTimestamp.kind == "ingestion")
            )
        ).scalars().all()
        assert len(row_ts) == 2   # two data rows in CSV

    # Upload the same file again — observation is now "unchanged", not new.
    r = await client.post(
        f"/api/v1/datasets/{ds_id}/upload",
        files={"file": ("data.csv", io.BytesIO(CSV), "text/csv")},
        headers=_csrf(client),
    )
    assert r.status_code == 202

    async with SessionLocal() as db:
        obs = (
            await db.execute(
                select(FileObservation).where(FileObservation.source_id == ds_id)
            )
        ).scalars().all()
        assert len(obs) == 1   # still one row
        assert obs[0].status == "unchanged"


@pytest.mark.asyncio
async def test_modified_file_creates_new_observation(
    client: AsyncClient, two_tenants: dict
) -> None:
    await _login(client, two_tenants["email_a"], two_tenants["password"])

    r = await client.post(
        "/api/v1/datasets",
        json={"name": "modified", "source_type": "csv", "config": {}},
        headers=_csrf(client),
    )
    ds_id = r.json()["id"]

    v1 = b"date,value\n2026-01-01,10\n"
    v2 = b"date,value\n2026-01-01,10\n2026-01-02,20\n"

    await client.post(
        f"/api/v1/datasets/{ds_id}/upload",
        files={"file": ("data.csv", io.BytesIO(v1), "text/csv")},
        headers=_csrf(client),
    )
    await client.post(
        f"/api/v1/datasets/{ds_id}/upload",
        files={"file": ("data.csv", io.BytesIO(v2), "text/csv")},
        headers=_csrf(client),
    )

    async with SessionLocal() as db:
        obs = (
            await db.execute(
                select(FileObservation)
                .where(FileObservation.source_id == ds_id)
                .order_by(FileObservation.first_seen_at)
            )
        ).scalars().all()
        assert len(obs) == 2
        assert obs[0].status == "new"
        assert obs[1].status == "changed"
        assert obs[0].content_hash != obs[1].content_hash