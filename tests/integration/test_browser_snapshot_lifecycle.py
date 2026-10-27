# tests/integration/test_browser_snapshot_lifecycle.py
"""
End-to-end: schedule → manifest → upload → ingestion worker → activation.
"""
from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import select

from app.db.models.agent import Agent
from app.db.models.browser import BrowserExtractionProfile, BrowserExtractionRun
from app.db.models.dataset import DataSource, StagedRow
from app.db.session import SessionLocal
from app.schemas.browser import (
    BrowserExtractionManifest,
    BrowserExtractionProfileCreate,
)
from app.services.browser import service as svc
from app.services.ingestion.service import run_job


@pytest.mark.asyncio
async def test_full_snapshot_lifecycle(two_tenants):
    ta, ua = two_tenants["tenant_a"], two_tenants["user_a"]
    async with SessionLocal() as db:
        src = DataSource(tenant_id=ta, name="S", source_type="agent")
        db.add(src)
        await db.flush()
        db.add(Agent(
            tenant_id=ta, name="ag", source_id=src.id, status="active",
        ))
        await db.flush()
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

        # Agent reports manifest.
        content = b"date,amount\n2026-01-01,100\n2026-01-02,200\n"
        digest = hashlib.sha256(content).hexdigest()
        manifest = BrowserExtractionManifest(
            run_id=run.run_id, sha256=digest, row_count=2,
            expected_columns=["date", "amount"], complete=True,
        )
        updated_run = await svc.handle_manifest(
            db, agent_job=job, manifest=manifest, agent_tenant_id=ta,
        )
        await db.commit()

        # Agent uploads bytes.
        ingestion_job = await svc.accept_upload(
            db, run=updated_run, agent_tenant_id=ta,
            content=content, content_hash=digest,
        )
        await db.commit()
        profile_id = prof.id
        run_id = run.id
        ingestion_job_id = ingestion_job.id

    # Worker runs the ingestion in a fresh session.
    async with SessionLocal() as db:
        await run_job(db, job_id=ingestion_job_id)

    # Verify final state.
    async with SessionLocal() as db:
        final_profile = await db.get(BrowserExtractionProfile, profile_id)
        assert final_profile is not None
        assert final_profile.active_ingestion_job_id == ingestion_job_id

        final_run = (
            await db.execute(
                select(BrowserExtractionRun).where(
                    BrowserExtractionRun.id == run_id,
                )
            )
        ).scalar_one()
        assert final_run.status == "active"
        assert final_run.activated_at is not None

        staged = (
            await db.execute(
                select(StagedRow).where(StagedRow.job_id == ingestion_job_id)
            )
        ).scalars().all()
        assert len(staged) == 2