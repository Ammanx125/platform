# tests/integration/test_active_snapshot_scoping.py
from __future__ import annotations

import json

import pytest

from app.db.models.browser import (
    BrowserAuthorization,
    BrowserExtractionProfile,
)
from app.db.models.dataset import DataSource, IngestionJob, StagedRow
from app.db.models.semantic import SemanticMapping
from app.db.session import SessionLocal
from app.services.analytics.context import EvaluationContext


async def _stage_row(db, *, tenant_id, source_id, job_id, n, value):
    row_data = {"amount": value, "note": f"row-{n}"}
    db.add(StagedRow(
        tenant_id=tenant_id, source_id=source_id, job_id=job_id,
        row_number=n, raw_data=row_data,
        search_text=json.dumps(row_data),
    ))
    await db.flush()


@pytest.mark.asyncio
async def test_only_active_snapshot_rows_are_read(two_tenants):
    ta = two_tenants["tenant_a"]

    async with SessionLocal() as db:
        src = DataSource(
            tenant_id=ta, name="snap", source_type="agent",
            snapshot_mode="replace_active",
        )
        db.add(src)
        await db.flush()

        job1 = IngestionJob(tenant_id=ta, source_id=src.id, status="succeeded")
        job2 = IngestionJob(tenant_id=ta, source_id=src.id, status="succeeded")
        db.add_all([job1, job2])
        await db.flush()

        # Old snapshot: 3 rows, values 1..3
        for i, v in enumerate([1.0, 2.0, 3.0], start=1):
            await _stage_row(
                db, tenant_id=ta, source_id=src.id, job_id=job1.id,
                n=i, value=v,
            )
        # New snapshot: 2 rows, values 10, 20
        for i, v in enumerate([10.0, 20.0], start=1):
            await _stage_row(
                db, tenant_id=ta, source_id=src.id, job_id=job2.id,
                n=i, value=v,
            )

        db.add(SemanticMapping(
            tenant_id=ta,
            source_id=src.id,
            source_column="amount",
            canonical_concept_key="Finance.Revenue",
            status="confirmed",
        ))

        auth = BrowserAuthorization(
            tenant_id=ta, source_id=src.id,
            origin="https://erp.example.com",
            approved_purpose="x", scope={}, status="active",
        )
        db.add(auth)
        await db.flush()
        prof = BrowserExtractionProfile(
            tenant_id=ta, source_id=src.id, authorization_id=auth.id,
            name="p", report_identifier="r",
            extraction_instructions={}, expected_columns=[],
            limits={}, snapshot_mode="replace_active",
            active_ingestion_job_id=job1.id,
        )
        db.add(prof)
        await db.commit()

        # Active = job1 → 3 rows visible
        ctx = await EvaluationContext.load(
            db, tenant_id=ta, source_ids=[src.id],
        )
        values = ctx.values_for("Finance.Revenue")
        assert len(ctx._rows) == 3
        assert sorted(values.values) == [1.0, 2.0, 3.0]

        # Swap active to job2 → 2 rows visible, job1 rows excluded
        prof.active_ingestion_job_id = job2.id
        await db.commit()

        ctx = await EvaluationContext.load(
            db, tenant_id=ta, source_ids=[src.id],
        )
        values = ctx.values_for("Finance.Revenue")
        assert len(ctx._rows) == 2
        assert sorted(values.values) == [10.0, 20.0]


@pytest.mark.asyncio
async def test_append_source_reads_all_rows(two_tenants):
    """
    Regression guard: the helper must not change behavior for append
    sources.
    """
    ta = two_tenants["tenant_a"]
    async with SessionLocal() as db:
        src = DataSource(
            tenant_id=ta, name="app", source_type="agent",
            snapshot_mode="append",
        )
        db.add(src)
        await db.flush()
        j1 = IngestionJob(tenant_id=ta, source_id=src.id, status="succeeded")
        j2 = IngestionJob(tenant_id=ta, source_id=src.id, status="succeeded")
        db.add_all([j1, j2])
        await db.flush()
        for i in range(3):
            await _stage_row(
                db, tenant_id=ta, source_id=src.id, job_id=j1.id,
                n=i + 1, value=float(i),
            )
        for i in range(2):
            await _stage_row(
                db, tenant_id=ta, source_id=src.id, job_id=j2.id,
                n=i + 4, value=float(10 + i),
            )
        await db.commit()

        ctx = await EvaluationContext.load(
            db, tenant_id=ta, source_ids=[src.id],
        )
        assert len(ctx._rows) == 5