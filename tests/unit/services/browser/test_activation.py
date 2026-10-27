# tests/unit/services/browser/test_activation.py
from __future__ import annotations

import hashlib

import pytest

from app.db.models.agent import Agent
from app.db.models.browser import BrowserExtractionRun
from app.db.models.dataset import DataSource, IngestionJob
from app.db.session import SessionLocal
from app.services.browser import service as svc
from app.services.browser.activation import (
    ActivationError,
    maybe_activate_snapshot,
    maybe_fail_browser_run_on_ingestion_failure,
)


async def _ready_run_with_ingestion(db, tenant_id, user_id, *, row_count=2):
    """
    Drive a run through schedule → manifest → upload so it's in
    'validating' with an ingestion_job_id. Returns (profile, run, job).
    """
    from app.db.models.dataset import DataSource
    from app.schemas.browser import (
        BrowserExtractionManifest,
        BrowserExtractionProfileCreate,
    )

    src = DataSource(tenant_id=tenant_id, name="S", source_type="agent")
    db.add(src)
    await db.flush()
    agent = Agent(
        tenant_id=tenant_id, name="ag", source_id=src.id, status="active",
    )
    db.add(agent)
    await db.flush()
    auth = await svc.create_authorization(
        db, tenant_id=tenant_id, source_id=src.id, user_id=user_id,
        origin="https://erp.example.com",
        approved_purpose="x", scope={}, evidence_ref=None,
    )
    prof = await svc.create_profile(
        db, tenant_id=tenant_id, source_id=src.id, user_id=user_id,
        payload=BrowserExtractionProfileCreate(
            name="p", report_identifier="r",
            authorization_id=auth.id, expected_columns=["date", "amount"],
        ),
    )
    await db.flush()
    job, run = await svc.schedule_extraction(
        db, tenant_id=tenant_id, profile_id=prof.id, user_id=user_id,
    )
    await db.commit()

    # Build a small CSV.
    buf = "date,amount\n"
    for i in range(row_count):
        buf += f"2026-01-0{i+1},{10 * (i+1)}\n"
    content = buf.encode()
    digest = hashlib.sha256(content).hexdigest()

    manifest = BrowserExtractionManifest(
        run_id=run.run_id, sha256=digest, row_count=row_count,
        expected_columns=["date", "amount"], complete=True,
    )
    updated_run = await svc.handle_manifest(
        db, agent_job=job, manifest=manifest, agent_tenant_id=tenant_id,
    )
    await db.commit()

    ingestion_job = await svc.accept_upload(
        db,
        run=updated_run,
        agent_tenant_id=tenant_id,
        content=content,
        content_hash=digest,
    )
    await db.commit()
    return prof, updated_run, ingestion_job


@pytest.mark.asyncio
async def test_activation_on_succeeded_ingestion(two_tenants):
    ta, ua = two_tenants["tenant_a"], two_tenants["user_a"]
    async with SessionLocal() as db:
        prof, run, ingestion_job = await _ready_run_with_ingestion(db, ta, ua)

        # Simulate the ingestion worker finishing.
        ingestion_job.status = "succeeded"
        await db.flush()

        activated = await maybe_activate_snapshot(db, ingestion_job=ingestion_job)
        await db.commit()

        assert activated is not None
        assert activated.status == "active"
        assert activated.activated_at is not None
        await db.refresh(prof)
        assert prof.active_ingestion_job_id == ingestion_job.id


@pytest.mark.asyncio
async def test_activation_supersedes_previous(two_tenants):
    ta, ua = two_tenants["tenant_a"], two_tenants["user_a"]
    async with SessionLocal() as db:
        # First snapshot fully active.
        prof, run1, job1 = await _ready_run_with_ingestion(db, ta, ua)
        job1.status = "succeeded"
        await db.flush()
        await maybe_activate_snapshot(db, ingestion_job=job1)
        await db.commit()

        # Now a second snapshot on the same profile.
        # Manually unlock profile for second run: the run1 is now 'active'
        # so the partial unique index no longer blocks.
        job2_agent, run2 = await svc.schedule_extraction(
            db, tenant_id=ta, profile_id=prof.id, user_id=ua,
        )
        await db.commit()

        import hashlib
        from app.schemas.browser import BrowserExtractionManifest

        content = b"date,amount\n2026-02-01,99\n"
        digest = hashlib.sha256(content).hexdigest()
        manifest = BrowserExtractionManifest(
            run_id=run2.run_id, sha256=digest, row_count=1,
            expected_columns=["date", "amount"], complete=True,
        )
        updated_run2 = await svc.handle_manifest(
            db, agent_job=job2_agent, manifest=manifest, agent_tenant_id=ta,
        )
        await db.commit()
        job2 = await svc.accept_upload(
            db, run=updated_run2, agent_tenant_id=ta,
            content=content, content_hash=digest,
        )
        job2.status = "succeeded"
        await db.flush()
        await maybe_activate_snapshot(db, ingestion_job=job2)
        await db.commit()

        await db.refresh(run1)
        await db.refresh(prof)
        assert run1.status == "superseded"
        assert run1.superseded_at is not None
        assert prof.active_ingestion_job_id == job2.id


@pytest.mark.asyncio
async def test_activation_is_noop_when_run_not_browser(two_tenants):
    ta = two_tenants["tenant_a"]
    async with SessionLocal() as db:
        src = DataSource(tenant_id=ta, name="csv", source_type="csv")
        db.add(src)
        await db.flush()
        job = IngestionJob(tenant_id=ta, source_id=src.id, status="succeeded")
        db.add(job)
        await db.flush()
        result = await maybe_activate_snapshot(db, ingestion_job=job)
        assert result is None


@pytest.mark.asyncio
async def test_activation_requires_sha_present(two_tenants):
    ta, ua = two_tenants["tenant_a"], two_tenants["user_a"]
    async with SessionLocal() as db:
        _prof, run, ingestion_job = await _ready_run_with_ingestion(db, ta, ua)
        run.actual_sha256 = None
        ingestion_job.status = "succeeded"
        await db.flush()
        with pytest.raises(ActivationError):
            await maybe_activate_snapshot(db, ingestion_job=ingestion_job)


@pytest.mark.asyncio
async def test_failure_on_ingestion_marks_run_failed(two_tenants):
    ta, ua = two_tenants["tenant_a"], two_tenants["user_a"]
    async with SessionLocal() as db:
        _prof, run, ingestion_job = await _ready_run_with_ingestion(db, ta, ua)
        ingestion_job.status = "failed"
        ingestion_job.error_message = "parse error"
        await db.flush()
        failed = await maybe_fail_browser_run_on_ingestion_failure(
            db, ingestion_job=ingestion_job,
        )
        await db.commit()
        assert failed is not None
        assert failed.status == "failed"
        assert "ingestion failed" in (failed.error or "")