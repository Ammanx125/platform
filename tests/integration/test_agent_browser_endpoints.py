# tests/integration/test_agent_browser_endpoints.py
"""
Exercise the two agent-facing endpoints for browser extraction:
  - POST /agents/{id}/jobs/{job_id}/result with a browser manifest
  - POST /agents/{id}/upload/{content_hash} for the snapshot
"""
from __future__ import annotations

import hashlib

import pytest
from httpx import AsyncClient

from app.db.models.agent import Agent
from app.db.models.browser import BrowserExtractionRun
from app.db.models.dataset import DataSource, IngestionJob
from app.db.session import SessionLocal
from app.schemas.browser import BrowserExtractionProfileCreate
from app.services.browser import service as svc


async def _bootstrap_browser_job(two_tenants):
    """
    Create an agent + auth + profile + a scheduled browser_extract job.
    Returns (agent_credential, agent_id, job_id, run_id, profile_id).
    """
    ta, ua = two_tenants["tenant_a"], two_tenants["user_a"]

    async with SessionLocal() as db:
        src = DataSource(tenant_id=ta, name="S", source_type="agent")
        db.add(src)
        await db.flush()
        # Create the agent by enrolling then registering via the service
        # layer directly (the API path would need a live HTTP call).
        agent, credential = await svc_agent_enroll_and_register(db, ta, src.id, ua)
        auth = await svc.create_authorization(
            db, tenant_id=ta, source_id=src.id, user_id=ua,
            origin="https://erp.example.com",
            approved_purpose="x", scope={}, evidence_ref=None,
        )
        prof = await svc.create_profile(
            db, tenant_id=ta, source_id=src.id, user_id=ua,
            payload=BrowserExtractionProfileCreate(
                name="p", report_identifier="r",
                authorization_id=auth.id, expected_columns=["date", "amount"],
            ),
        )
        await db.flush()
        job, run = await svc.schedule_extraction(
            db, tenant_id=ta, profile_id=prof.id, user_id=ua,
        )
        await db.commit()
        return credential, agent.id, job.id, run.run_id, prof.id


async def svc_agent_enroll_and_register(db, tenant_id, source_id, user_id):
    from app.services.agents import service as agents_service
    agent, token = await agents_service.create_agent_enrollment(
        db, tenant_id=tenant_id, source_id=source_id,
        name="ag", description=None, enrolled_by_user_id=user_id,
    )
    await db.flush()
    agent, credential = await agents_service.register_agent(
        db, enrollment_token=token, agent_metadata={},
    )
    await db.flush()
    return agent, credential


@pytest.mark.asyncio
async def test_browser_manifest_and_upload_endpoints(two_tenants, client: AsyncClient):
    credential, agent_id, job_id, run_id, _profile_id = await _bootstrap_browser_job(two_tenants)

    # Claim the job (simulating the agent's poll).
    r = await client.get(
        f"/api/v1/agents/{agent_id}/jobs",
        headers={"Authorization": f"Bearer {credential}"},
    )
    assert r.status_code == 200

    # Build the manifest and POST to /result.
    content = b"date,amount\n2026-01-01,100\n2026-01-02,200\n"
    digest = hashlib.sha256(content).hexdigest()
    manifest = {
        "run_id": run_id,
        "sha256": digest,
        "row_count": 2,
        "expected_columns": ["date", "amount"],
        "complete": True,
    }
    r = await client.post(
        f"/api/v1/agents/{agent_id}/jobs/{job_id}/result",
        headers={"Authorization": f"Bearer {credential}"},
        json={"status": "completed", "result": manifest, "error": None},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["next_action"] == "upload"
    assert body["run_id"] == run_id

    # Upload the bytes to /upload/{content_hash}.
    files = {"file": ("snapshot.csv", content, "text/csv")}
    r = await client.post(
        f"/api/v1/agents/{agent_id}/upload/{digest}",
        headers={"Authorization": f"Bearer {credential}"},
        files=files,
    )
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert body["status"] == "accepted"
    ingestion_job_id = body["ingestion_job_id"]

    # Verify the DB reflects what we expect.
    async with SessionLocal() as db:
        run = (
            await db.execute(
                BrowserExtractionRun.__table__.select().where(
                    BrowserExtractionRun.run_id == run_id
                )
            )
        ).first()
        assert run is not None
        assert str(run.ingestion_job_id) == ingestion_job_id
        assert run.status == "validating"


@pytest.mark.asyncio
async def test_browser_manifest_rejects_wrong_run_id(two_tenants, client: AsyncClient):
    credential, agent_id, job_id, _run_id, _pid = await _bootstrap_browser_job(two_tenants)

    await client.get(
        f"/api/v1/agents/{agent_id}/jobs",
        headers={"Authorization": f"Bearer {credential}"},
    )

    bad_manifest = {
        "run_id": "not-the-real-run-id",
        "sha256": "a" * 64,
        "row_count": 1,
        "expected_columns": ["date", "amount"],
        "complete": True,
    }
    r = await client.post(
        f"/api/v1/agents/{agent_id}/jobs/{job_id}/result",
        headers={"Authorization": f"Bearer {credential}"},
        json={"status": "completed", "result": bad_manifest, "error": None},
    )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_browser_job_failure_marks_run_failed(two_tenants, client: AsyncClient):
    credential, agent_id, job_id, run_id, _pid = await _bootstrap_browser_job(two_tenants)
    await client.get(
        f"/api/v1/agents/{agent_id}/jobs",
        headers={"Authorization": f"Bearer {credential}"},
    )

    r = await client.post(
        f"/api/v1/agents/{agent_id}/jobs/{job_id}/result",
        headers={"Authorization": f"Bearer {credential}"},
        json={"status": "failed", "result": {}, "error": "session expired"},
    )
    assert r.status_code == 200

    async with SessionLocal() as db:
        run = (
            await db.execute(
                BrowserExtractionRun.__table__.select().where(
                    BrowserExtractionRun.run_id == run_id
                )
            )
        ).first()
        assert run is not None
        assert run.status == "failed"
        assert "session expired" in (run.error or "")