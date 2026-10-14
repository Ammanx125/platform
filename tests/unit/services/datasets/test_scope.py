# tests/unit/services/datasets/test_scope.py
from __future__ import annotations

import pytest

from app.db.models.browser import (
    BrowserAuthorization,
    BrowserExtractionProfile,
)
from app.db.models.dataset import DataSource, IngestionJob
from app.db.session import SessionLocal
from app.services.datasets.scope import (
    SnapshotScopeError,
    active_snapshot_filter,
)


async def _mk_source(db, tenant_id, *, name, mode):
    src = DataSource(
        tenant_id=tenant_id, name=name, source_type="agent",
        snapshot_mode=mode,
    )
    db.add(src)
    await db.flush()
    return src


async def _mk_browser_profile(db, tenant_id, *, source_id, active_job_id=None):
    auth = BrowserAuthorization(
        tenant_id=tenant_id, source_id=source_id,
        origin="https://erp.example.com",
        approved_purpose="x", scope={}, status="active",
    )
    db.add(auth)
    await db.flush()
    prof = BrowserExtractionProfile(
        tenant_id=tenant_id, source_id=source_id,
        authorization_id=auth.id,
        name=f"p-{source_id}",
        report_identifier="r",
        extraction_instructions={}, expected_columns=[],
        limits={}, snapshot_mode="replace_active",
        active_ingestion_job_id=active_job_id,
    )
    db.add(prof)
    await db.flush()
    return prof


@pytest.mark.asyncio
async def test_append_returns_no_clauses(two_tenants):
    ta = two_tenants["tenant_a"]
    async with SessionLocal() as db:
        s = await _mk_source(db, ta, name="A", mode="append")
        await db.commit()
        clauses = await active_snapshot_filter(
            db, tenant_id=ta, source_ids=[s.id],
        )
        assert clauses == []


@pytest.mark.asyncio
async def test_replace_active_requires_pointer(two_tenants):
    ta = two_tenants["tenant_a"]
    async with SessionLocal() as db:
        s = await _mk_source(db, ta, name="B", mode="replace_active")
        await _mk_browser_profile(db, ta, source_id=s.id, active_job_id=None)
        await db.commit()
        with pytest.raises(SnapshotScopeError):
            await active_snapshot_filter(db, tenant_id=ta, source_ids=[s.id])


@pytest.mark.asyncio
async def test_replace_active_returns_job_clause(two_tenants):
    ta = two_tenants["tenant_a"]
    async with SessionLocal() as db:
        s = await _mk_source(db, ta, name="C", mode="replace_active")
        job = IngestionJob(tenant_id=ta, source_id=s.id, status="succeeded")
        db.add(job)
        await db.flush()
        await _mk_browser_profile(db, ta, source_id=s.id, active_job_id=job.id)
        await db.commit()

        clauses = await active_snapshot_filter(
            db, tenant_id=ta, source_ids=[s.id],
        )
        assert len(clauses) == 1
        # Sanity check the clause references StagedRow.job_id.
        assert "job_id" in str(clauses[0]).lower() or "staged_rows" in str(clauses[0]).lower()


@pytest.mark.asyncio
async def test_mixed_modes_raise(two_tenants):
    ta = two_tenants["tenant_a"]
    async with SessionLocal() as db:
        a = await _mk_source(db, ta, name="mix-a", mode="append")
        b = await _mk_source(db, ta, name="mix-b", mode="replace_active")
        job = IngestionJob(tenant_id=ta, source_id=b.id, status="succeeded")
        db.add(job)
        await db.flush()
        await _mk_browser_profile(db, ta, source_id=b.id, active_job_id=job.id)
        await db.commit()

        with pytest.raises(SnapshotScopeError):
            await active_snapshot_filter(
                db, tenant_id=ta, source_ids=[a.id, b.id],
            )


@pytest.mark.asyncio
async def test_foreign_source_rejected(two_tenants):
    ta, tb = two_tenants["tenant_a"], two_tenants["tenant_b"]
    async with SessionLocal() as db:
        s_b = await _mk_source(db, tb, name="foreign", mode="append")
        await db.commit()
        with pytest.raises(SnapshotScopeError):
            await active_snapshot_filter(
                db, tenant_id=ta, source_ids=[s_b.id],
            )