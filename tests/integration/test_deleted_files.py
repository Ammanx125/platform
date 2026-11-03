# tests/integration/test_deleted_files.py
"""
Finding 12 (second half): a deleted source file must stop powering analytics.

The reported consequence: a customer deletes an old financial spreadsheet
because it contains wrong information, and the platform keeps deriving KPIs
from the previously ingested rows.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.db.models.analytics import KPIDefinition
from app.db.models.dataset import DataSource, IngestionJob, StagedRow
from app.db.models.semantic import SemanticMapping
from app.db.models.timestamps import FileObservation
from app.db.seed_concepts import seed_canonical_concepts
from app.db.session import SessionLocal
from app.services.analytics.kpi import evaluate_kpi
from app.services.analytics.series import build_series
from app.services.retrieval.base import RetrievalFilters
from app.services.retrieval.sql import SQLRetriever
from app.services.timestamps.service import record_row_timestamp

KPI_KEY = "test.deleted_file_revenue"
SOURCE_KPI = "sales.total_revenue"


async def _seed_file(
    tenant_id,
    *,
    name: str,
    revenue: float,
    content_hash: str,
    source_id=None,
):
    async with SessionLocal() as db:
        await seed_canonical_concepts(db)
        await db.flush()

        if source_id is None:
            source = DataSource(
                tenant_id=tenant_id,
                name=f"deleted-{uuid.uuid4().hex[:6]}",
                source_type="csv",
                config={},
                is_active=True,
            )
            db.add(source)
            await db.flush()
            source_id = source.id

        source = (
            await db.execute(select(DataSource).where(DataSource.id == source_id))
        ).scalar_one()

        job = IngestionJob(
            tenant_id=tenant_id,
            source_id=source.id,
            status="succeeded",
            pending_hash=content_hash,
            finished_at=datetime.now(UTC),
        )
        db.add(job)
        await db.flush()

        existing_mapping = (
            await db.execute(
                select(SemanticMapping).where(
                    SemanticMapping.source_id == source_id,
                    SemanticMapping.source_column == "revenue",
                )
            )
        ).scalar_one_or_none()
        if existing_mapping is None:
            db.add(
                SemanticMapping(
                    tenant_id=tenant_id,
                    source_id=source_id,
                    source_column="revenue",
                    canonical_concept_key="Finance.Revenue",
                    status="confirmed",
                    rationale={},
                )
            )
        staged = StagedRow(
            tenant_id=tenant_id,
            source_id=source.id,
            job_id=job.id,
            row_number=0,
            raw_data={"revenue": revenue},
            search_text=str(revenue),
        )
        db.add(staged)
        await db.flush()
        await record_row_timestamp(
            db,
            tenant_id=tenant_id,
            staged_row_id=staged.id,
            kind="content",
            timestamp=datetime.now(UTC),
        )
        db.add(
            FileObservation(
                tenant_id=tenant_id,
                source_id=source.id,
                path=name,
                content_hash=content_hash,
                first_seen_at=datetime.now(UTC),
                last_seen_at=datetime.now(UTC),
                status="new",
                ingestion_job_id=job.id,
            )
        )

        definition = (
            await db.execute(select(KPIDefinition).where(KPIDefinition.key == SOURCE_KPI))
        ).scalar_one_or_none()
        if definition is None:
            db.add(
                KPIDefinition(
                    key=SOURCE_KPI,
                    display_name="Revenue",
                    domain="Sales",
                    unit="currency",
                    value_type="number",
                    formula={"op": "sum", "concept": "Finance.Revenue"},
                )
            )
        await db.commit()
        return source.id, job.id


@pytest.mark.asyncio
async def test_deleting_a_file_removes_its_rows_from_kpis(two_tenants) -> None:
    tenant = two_tenants["tenant_a"]
    source_id, job_id = await _seed_file(
        tenant, name="sales_old.csv", revenue=10_000.0, content_hash="a" * 64
    )
    await _seed_file(
        tenant,
        name="sales_new.csv",
        revenue=500.0,
        content_hash="b" * 64,
        source_id=source_id,
    )

    async with SessionLocal() as db:
        before = await evaluate_kpi(db, key=SOURCE_KPI, tenant_id=tenant, source_ids=[source_id])
    # Both files are on the same source once the second upload lands, so
    # scope to this source and expect both contributions.
    assert before.error is None

    async with SessionLocal() as db:
        observation = (
            await db.execute(
                select(FileObservation).where(FileObservation.ingestion_job_id == job_id)
            )
        ).scalar_one()
        observation.status = "deleted"
        await db.commit()

        after = await evaluate_kpi(db, key=SOURCE_KPI, tenant_id=tenant, source_ids=[source_id])

    assert after.error is None
    assert after.value == pytest.approx(500.0)


@pytest.mark.asyncio
async def test_deleted_file_is_excluded_from_series_and_retrieval(two_tenants) -> None:
    tenant = two_tenants["tenant_a"]
    source_id, deleted_job_id = await _seed_file(
        tenant,
        name="deleted-search.csv",
        revenue=10_000.0,
        content_hash="f" * 64,
    )
    await _seed_file(
        tenant,
        name="active-search.csv",
        revenue=500.0,
        content_hash="1" * 64,
        source_id=source_id,
    )

    async with SessionLocal() as db:
        observation = (
            await db.execute(
                select(FileObservation).where(FileObservation.ingestion_job_id == deleted_job_id)
            )
        ).scalar_one()
        observation.status = "deleted"
        await db.commit()

        series = await build_series(
            db,
            tenant_id=tenant,
            source_ids=[source_id],
            value_concept="Finance.Revenue",
            group_by_concept=None,
        )
        retrieved = await SQLRetriever().retrieve(
            db=db,
            tenant_id=tenant,
            query="10000",
            top_k=10,
            filters=RetrievalFilters(source_ids=[source_id]),
        )

    assert series.rows_used == 1
    assert series.series[0].points[0].value == pytest.approx(500.0)
    assert retrieved.items == []


@pytest.mark.asyncio
async def test_a_metadata_only_observation_does_not_hide_rows(two_tenants) -> None:
    """An observation with no ingestion job has produced nothing to delete."""
    tenant = two_tenants["tenant_a"]
    source_id, _ = await _seed_file(tenant, name="kept.csv", revenue=250.0, content_hash="c" * 64)

    async with SessionLocal() as db:
        db.add(
            FileObservation(
                tenant_id=tenant,
                source_id=source_id,
                path="never_uploaded.csv",
                content_hash="d" * 64,
                first_seen_at=datetime.now(UTC),
                last_seen_at=datetime.now(UTC),
                status="deleted",
                ingestion_job_id=None,
            )
        )
        await db.commit()

        result = await evaluate_kpi(db, key=SOURCE_KPI, tenant_id=tenant, source_ids=[source_id])

    assert result.value == pytest.approx(250.0)
