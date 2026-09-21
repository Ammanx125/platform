# tests/integration/test_pdf_ingestion.py
import io
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.db.models.dataset import IngestionJob
from app.db.models.knowledge import Chunk, Document
from app.db.session import SessionLocal
from app.services.ingestion.service import run_job

FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample.pdf"


def _csrf(client: AsyncClient) -> dict[str, str]:
    t = client.cookies.get("sansa_csrf")
    return {"X-CSRF-Token": t} if t else {}


async def _login(client: AsyncClient, email: str, password: str) -> None:
    await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )


@pytest.mark.asyncio
async def test_pdf_upload_creates_document(
    client: AsyncClient, two_tenants: dict
) -> None:
    await _login(client, two_tenants["email_a"], two_tenants["password"])

    r = await client.post(
        "/api/v1/datasets",
        json={"name": "PDF test", "source_type": "pdf", "config": {}},
        headers=_csrf(client),
    )
    ds_id = r.json()["id"]

    content = FIXTURE.read_bytes()
    r = await client.post(
        f"/api/v1/datasets/{ds_id}/upload",
        files={"file": ("sample.pdf", io.BytesIO(content), "application/pdf")},
        headers=_csrf(client),
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["id"]

    async with SessionLocal() as db:
        await run_job(db, job_id=job_id)

    async with SessionLocal() as db:
        job = (
            await db.execute(select(IngestionJob).where(IngestionJob.id == job_id))
        ).scalar_one()
        assert job.status == "succeeded", job.error_message

        docs = (
            await db.execute(
                select(Document).where(Document.source_id == ds_id)
            )
        ).scalars().all()
        assert len(docs) == 1
        doc = docs[0]
        assert doc.status == "ready"
        assert doc.content_type == "pdf"
        assert doc.chunk_count >= 1

        chunks = (
            await db.execute(
                select(Chunk).where(Chunk.document_id == doc.id)
            )
        ).scalars().all()
        assert len(chunks) == doc.chunk_count
        # At least one chunk should mention "supplier" — proves extraction worked.
        assert any("supplier" in c.text.lower() for c in chunks)


@pytest.mark.asyncio
async def test_pdf_rejects_wrong_extension(
    client: AsyncClient, two_tenants: dict
) -> None:
    await _login(client, two_tenants["email_a"], two_tenants["password"])
    r = await client.post(
        "/api/v1/datasets",
        json={"name": "x", "source_type": "pdf", "config": {}},
        headers=_csrf(client),
    )
    ds_id = r.json()["id"]
    r = await client.post(
        f"/api/v1/datasets/{ds_id}/upload",
        files={"file": ("notpdf.txt", io.BytesIO(b"hello"), "text/plain")},
        headers=_csrf(client),
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_pdf_rejects_empty_file(
    client: AsyncClient, two_tenants: dict
) -> None:
    await _login(client, two_tenants["email_a"], two_tenants["password"])
    r = await client.post(
        "/api/v1/datasets",
        json={"name": "y", "source_type": "pdf", "config": {}},
        headers=_csrf(client),
    )
    ds_id = r.json()["id"]
    r = await client.post(
        f"/api/v1/datasets/{ds_id}/upload",
        files={"file": ("empty.pdf", io.BytesIO(b""), "application/pdf")},
        headers=_csrf(client),
    )
    # Uploaded OK (we don't validate content at upload time), but the job fails.
    assert r.status_code == 202
    job_id = r.json()["id"]
    async with SessionLocal() as db:
        await run_job(db, job_id=job_id)
    async with SessionLocal() as db:
        job = (
            await db.execute(select(IngestionJob).where(IngestionJob.id == job_id))
        ).scalar_one()
        assert job.status == "failed"