# tests/integration/test_agent_jobs.py
"""
Agent job lifecycle, classification, and content upload.
"""
from __future__ import annotations

import hashlib
import io
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.db.models.agent import PendingFileUpload
from app.db.models.dataset import DataSource, IngestionJob, StagedRow
from app.db.models.timestamps import RowTimestamp
from app.db.session import SessionLocal
from app.services.agents.classifier import classify_observations
from app.workers.tasks.ingestion import run_ingestion_once


def _csrf(client: AsyncClient) -> dict[str, str]:
    t = client.cookies.get("sansa_csrf")
    return {"X-CSRF-Token": t} if t else {}


async def _login(client: AsyncClient, email: str, password: str) -> None:
    await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )


async def _enroll_and_register(client: AsyncClient, tenant_env: dict) -> tuple[str, str, str]:
    """Returns (agent_id, credential, source_id)."""
    await _login(client, tenant_env["email_a"], tenant_env["password"])

    r = await client.post(
        "/api/v1/datasets",
        json={
            "name": f"Agent src {uuid4().hex}",
            "source_type": "agent",
            "config": {},
        },
        headers=_csrf(client),
    )
    assert r.status_code == 201, r.text
    source_id = r.json()["id"]

    r = await client.post(
        "/api/v1/agents/enroll",
        json={"name": "Test", "source_id": source_id},
        headers=_csrf(client),
    )
    assert r.status_code == 201, r.text
    agent_id = r.json()["agent_id"]
    token = r.json()["enrollment_token"]

    r = await client.post(
        "/api/v1/agents/register",
        json={"enrollment_token": token, "agent_metadata": {}},
    )
    credential = r.json()["credential"]
    return agent_id, credential, source_id


@pytest.mark.asyncio
async def test_classification_and_ingest_flow(
    client: AsyncClient, two_tenants: dict
) -> None:
    agent_id, credential, source_id = await _enroll_and_register(client, two_tenants)
    auth = {"Authorization": f"Bearer {credential}"}

    async with SessionLocal() as db:
        source = (
            await db.execute(
                select(DataSource).where(DataSource.id == UUID(source_id))
            )
        ).scalar_one()
        source.config = {
            "content_date_columns": {"reports/purchases.csv": "service_date"}
        }
        await db.commit()

    csv_content = (
        b"service_date,supplier,price\n"
        b"2025-04-01,Acme,10\n"
        b"2025-04-02,Beacon,20\n"
    )
    csv_hash = hashlib.sha256(csv_content).hexdigest()

    # Sync a batch of observations.
    r = await client.post(
        f"/api/v1/agents/{agent_id}/sync",
        json={
            "files": [
                {
                    "path": "reports/purchases.csv",
                    "content_hash": csv_hash,
                    "byte_size": len(csv_content),
                    "status": "new",
                },
                {
                    "path": "images/logo.png",
                    "content_hash": "0" * 64,
                    "byte_size": 500,
                    "status": "new",
                },
            ]
        },
        headers=auth,
    )
    assert r.status_code == 200

    async with SessionLocal() as db:
        foreign_classification = await classify_observations(
            db,
            tenant_id=two_tenants["tenant_b"],
            source_id=UUID(source_id),
        )
    assert foreign_classification.counts() == {
        "ready": 0,
        "needs_review": 0,
        "unsupported": 0,
    }

    # Classification shows one ready, one unsupported.
    r = await client.get(f"/api/v1/datasets/{source_id}/observations/classification")
    assert r.status_code == 200
    body = r.json()
    assert body["counts"]["ready"] == 1
    assert body["counts"]["unsupported"] == 1

    # Trigger ingest.
    r = await client.post(
        f"/api/v1/datasets/{source_id}/ingest",
        headers=_csrf(client),
    )
    assert r.status_code == 202, r.text
    assert r.json()["agent_job_count"] == 1, r.text

    # Agent polls for jobs.
    r = await client.get(f"/api/v1/agents/{agent_id}/jobs", headers=auth)
    assert r.status_code == 200
    jobs = r.json()
    assert len(jobs) == 1
    job = jobs[0]
    assert job["job_type"] == "upload_file"
    assert job["params"]["path"] == "reports/purchases.csv"

    # Another agent in the same tenant cannot fulfill this agent's upload.
    other_agent_id, other_credential, _ = await _enroll_and_register(
        client, two_tenants
    )
    r = await client.post(
        f"/api/v1/agents/{other_agent_id}/upload/{csv_hash}",
        files={"file": ("upload.bin", io.BytesIO(csv_content), "text/csv")},
        headers={"Authorization": f"Bearer {other_credential}"},
    )
    assert r.status_code == 404

    # A mismatched payload is rejected before it can be stored or delivered.
    r = await client.post(
        f"/api/v1/agents/{agent_id}/upload/{csv_hash}",
        files={
            "file": (
                "upload.bin",
                io.BytesIO(csv_content + b"tampered"),
                "text/csv",
            )
        },
        headers=auth,
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "content hash mismatch"

    async with SessionLocal() as db:
        pending_upload = (
            await db.execute(
                select(PendingFileUpload).where(
                    PendingFileUpload.expected_hash == csv_hash,
                    PendingFileUpload.tenant_id == two_tenants["tenant_a"],
                )
            )
        ).scalar_one()
        ingestion_job = (
            await db.execute(
                select(IngestionJob).where(
                    IngestionJob.id == pending_upload.job_id
                )
            )
        ).scalar_one()
        assert pending_upload.status == "pending"
        assert ingestion_job.storage_key is None

    # The owning agent can still upload the expected bytes.
    r = await client.post(
        f"/api/v1/agents/{agent_id}/upload/{csv_hash}",
        files={"file": ("upload.bin", io.BytesIO(csv_content), "text/csv")},
        headers=auth,
    )
    assert r.status_code == 201, r.text

    # Agent reports the job result.
    r = await client.post(
        f"/api/v1/agents/{agent_id}/jobs/{job['id']}/result",
        json={"status": "completed", "result": {"actual_hash": csv_hash}},
        headers=auth,
    )
    assert r.status_code == 200

    # File delivery returns before the worker performs expensive ingestion.
    async with SessionLocal() as db:
        processed = await run_ingestion_once(db)
        await db.commit()
    assert processed == 1

    r = await client.get(f"/api/v1/datasets/{source_id}/jobs")
    assert r.status_code == 200
    jobs_resp = r.json()
    assert len(jobs_resp) >= 1
    assert any(j["status"] == "succeeded" for j in jobs_resp)

    async with SessionLocal() as db:
        staged = (
            await db.execute(
                select(StagedRow).where(
                    StagedRow.source_id == UUID(source_id)
                )
            )
        ).scalars().all()
        assert len(staged) == 2
        content_timestamps = (
            await db.execute(
                select(RowTimestamp)
                .where(
                    RowTimestamp.staged_row_id.in_([row.id for row in staged]),
                    RowTimestamp.kind == "content",
                )
                .order_by(RowTimestamp.timestamp)
            )
        ).scalars().all()
        assert [timestamp.timestamp.date().isoformat() for timestamp in content_timestamps] == [
            "2025-04-01",
            "2025-04-02",
        ]
        assert all(timestamp.context == "service_date" for timestamp in content_timestamps)

    # A repeated request must not stage the same file hash a second time.
    r = await client.post(
        f"/api/v1/datasets/{source_id}/ingest",
        headers=_csrf(client),
    )
    assert r.status_code == 400
    assert "already queued or ingested" in r.json()["detail"]["message"]


@pytest.mark.asyncio
async def test_failed_agent_upload_marks_ingestion_job_failed(
    client: AsyncClient, two_tenants: dict
) -> None:
    agent_id, credential, source_id = await _enroll_and_register(client, two_tenants)
    auth = {"Authorization": f"Bearer {credential}"}
    content_hash = hashlib.sha256(b"data\nvalue\n1\n").hexdigest()

    sync = await client.post(
        f"/api/v1/agents/{agent_id}/sync",
        json={
            "files": [{
                "path": "failed.csv",
                "content_hash": content_hash,
                "byte_size": 13,
                "status": "new",
            }]
        },
        headers=auth,
    )
    assert sync.status_code == 200

    ingest = await client.post(
        f"/api/v1/datasets/{source_id}/ingest",
        headers=_csrf(client),
    )
    assert ingest.status_code == 202, ingest.text

    claimed = await client.get(
        f"/api/v1/agents/{agent_id}/jobs", headers=auth
    )
    assert claimed.status_code == 200
    agent_job = claimed.json()[0]

    result = await client.post(
        f"/api/v1/agents/{agent_id}/jobs/{agent_job['id']}/result",
        json={
            "status": "failed",
            "result": {},
            "error": "file is no longer present",
        },
        headers=auth,
    )
    assert result.status_code == 200

    jobs = await client.get(f"/api/v1/datasets/{source_id}/jobs")
    assert jobs.status_code == 200
    assert len(jobs.json()) == 1
    assert jobs.json()[0]["status"] == "failed"
    assert jobs.json()[0]["error_message"] == "no files were delivered by the agent"


@pytest.mark.asyncio
async def test_worker_skips_agent_ingestion_until_content_is_uploaded(
    two_tenants: dict,
) -> None:
    async with SessionLocal() as db:
        source = DataSource(
            tenant_id=two_tenants["tenant_a"],
            name="Agent awaiting upload",
            source_type="agent",
            config={},
        )
        db.add(source)
        await db.flush()
        job = IngestionJob(
            tenant_id=two_tenants["tenant_a"],
            source_id=source.id,
            status="pending",
        )
        db.add(job)
        await db.flush()

        processed = await run_ingestion_once(db)

        assert processed == 0
        assert job.status == "pending"