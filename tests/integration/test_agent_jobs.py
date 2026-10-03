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
from app.db.models.dataset import DataSource, IngestionJob
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

    csv_content = b"supplier,price\nAcme,10\nBeacon,20\n"
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

    # Ingestion should have run; check the job is no longer pending.
    r = await client.get(f"/api/v1/datasets/{source_id}/jobs")
    assert r.status_code == 200
    jobs_resp = r.json()
    assert len(jobs_resp) >= 1
    # The first ingestion job should now be succeeded (or at least not pending).
    assert any(j["status"] in ("succeeded", "running") for j in jobs_resp)


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