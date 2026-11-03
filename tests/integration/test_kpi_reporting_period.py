# tests/integration/test_kpi_reporting_period.py
"""
Finding 7 (second half): a KPI's number must be tied to a reporting period.

An all-time sum and a monthly sum are different answers to the same
question. Before this, every KPI summed everything ever staged.
"""

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from app.db.models.analytics import KPIDefinition
from app.db.models.dataset import DataSource, IngestionJob, StagedRow
from app.db.models.semantic import SemanticMapping
from app.db.models.timestamps import FileObservation
from app.db.seed_concepts import seed_canonical_concepts
from app.db.seed_kpis import seed_kpis
from app.db.session import SessionLocal
from app.services.analytics.kpi import evaluate_kpi
from app.services.timestamps.service import record_row_timestamp

KPI_KEY = "test.period_revenue"


async def _seed(tenant_id, user_id, rows: list[tuple[date, float]]) -> uuid.UUID:
    """One source holding (date, revenue) rows, mapped to Finance.Revenue."""
    async with SessionLocal() as db:
        await seed_canonical_concepts(db)
        await seed_kpis(db)
        await db.flush()

        source = DataSource(
            tenant_id=tenant_id,
            name=f"period-{uuid.uuid4().hex[:6]}",
            source_type="csv",
            config={},
            is_active=True,
        )
        db.add(source)
        await db.flush()

        job = IngestionJob(
            tenant_id=tenant_id,
            source_id=source.id,
            status="succeeded",
            finished_at=datetime.now(UTC),
        )
        db.add(job)
        await db.flush()

        db.add(
            SemanticMapping(
                tenant_id=tenant_id,
                source_id=source.id,
                source_column="revenue",
                canonical_concept_key="Finance.Revenue",
                status="confirmed",
                rationale={},
            )
        )

        for index, (when, value) in enumerate(rows):
            staged = StagedRow(
                tenant_id=tenant_id,
                source_id=source.id,
                job_id=job.id,
                row_number=index,
                raw_data={"revenue": value},
                search_text=str(value),
            )
            db.add(staged)
            await db.flush()
            await record_row_timestamp(
                db,
                tenant_id=tenant_id,
                staged_row_id=staged.id,
                kind="content",
                timestamp=datetime(when.year, when.month, when.day, tzinfo=UTC),
            )

        definition = (
            await db.execute(select(KPIDefinition).where(KPIDefinition.key == KPI_KEY))
        ).scalar_one_or_none()
        if definition is None:
            db.add(
                KPIDefinition(
                    key=KPI_KEY,
                    display_name="Period Revenue",
                    domain="Finance",
                    unit="currency",
                    value_type="number",
                    formula={"op": "sum", "concept": "Finance.Revenue"},
                )
            )
        await db.commit()
        return source.id


async def _evaluate(tenant_id, source_id, period: str, count: int = 1):
    async with SessionLocal() as db:
        definition = (
            await db.execute(select(KPIDefinition).where(KPIDefinition.key == KPI_KEY))
        ).scalar_one()
        definition.period = period
        definition.period_count = count
        await db.commit()

        return await evaluate_kpi(
            db,
            key=KPI_KEY,
            tenant_id=tenant_id,
            source_ids=[source_id],
        )


@pytest.mark.asyncio
async def test_all_time_and_monthly_are_different_answers(two_tenants) -> None:
    tenant = two_tenants["tenant_a"]
    rows = [
        (date(2026, 1, 15), 1_000.0),
        (date(2026, 2, 10), 2_000.0),
        (date(2026, 2, 20), 4_000.0),
        (date(2026, 3, 5), 8_000.0),
    ]
    source_id = await _seed(tenant, two_tenants["user_a"], rows)

    all_time = await _evaluate(tenant, source_id, "all_time")
    last_month = await _evaluate(tenant, source_id, "month", count=1)
    last_two = await _evaluate(tenant, source_id, "month", count=2)

    assert all_time.value == pytest.approx(15_000)
    # Newest data point is March, so "last month" is March.
    assert last_month.value == pytest.approx(8_000)
    # Anchoored at March, "last 2 months" is February + March.
    assert last_two.value == pytest.approx(14_000)


@pytest.mark.asyncio
async def test_period_is_reported_in_the_trace(two_tenants) -> None:
    tenant = two_tenants["tenant_a"]
    source_id = await _seed(tenant, two_tenants["user_a"], [(date(2026, 5, 1), 10.0)])

    result = await _evaluate(tenant, source_id, "month")

    print("RESULT ERROR:", result.error)
    trace = result.formula_trace
    assert trace["period"] == "month"
    assert trace["period_start"] == "2026-05-01"
    assert trace["period_end"] == "2026-06-01"


@pytest.mark.asyncio
async def test_rows_without_a_date_are_excluded_from_a_bounded_period(
    two_tenants,
) -> None:
    """
    A row whose date is unknown cannot be claimed to fall inside a month.
    Including it would make a monthly figure quietly include all history.
    """

    tenant = two_tenants["tenant_a"]
    source_id = await _seed(tenant, two_tenants["user_a"], [(date(2026, 5, 1), 100.0)])

    async with SessionLocal() as db:
        job = (
            await db.execute(select(IngestionJob).where(IngestionJob.source_id == source_id))
        ).scalar_one()
        undated = StagedRow(
            tenant_id=tenant,
            source_id=source_id,
            job_id=job.id,
            row_number=999,
            raw_data={"revenue": 5_000.0},
            search_text="5000",
        )
        db.add(undated)
        await db.commit()

    monthly = await _evaluate(tenant, source_id, "month")
    all_time = await _evaluate(tenant, source_id, "all_time")

    assert monthly.value == pytest.approx(100.0)
    assert all_time.value == pytest.approx(5_100.0)


@pytest.mark.asyncio
async def test_deleted_latest_file_does_not_anchor_reporting_period(two_tenants) -> None:
    tenant = two_tenants["tenant_a"]
    older_source = await _seed(tenant, two_tenants["user_a"], [(date(2026, 1, 15), 100.0)])
    newer_source = await _seed(tenant, two_tenants["user_a"], [(date(2026, 3, 5), 900.0)])

    async with SessionLocal() as db:
        latest_job = (
            await db.execute(select(IngestionJob).where(IngestionJob.source_id == newer_source))
        ).scalar_one()
        db.add(
            FileObservation(
                tenant_id=tenant,
                source_id=newer_source,
                path="deleted-latest.csv",
                content_hash="e" * 64,
                first_seen_at=datetime.now(UTC),
                last_seen_at=datetime.now(UTC),
                status="deleted",
                ingestion_job_id=latest_job.id,
            )
        )
        definition = (
            await db.execute(select(KPIDefinition).where(KPIDefinition.key == KPI_KEY))
        ).scalar_one()
        definition.period = "month"
        definition.period_count = 1
        await db.commit()

        result = await evaluate_kpi(
            db,
            key=KPI_KEY,
            tenant_id=tenant,
            source_ids=[older_source, newer_source],
        )

    assert result.error is None
    assert result.value == pytest.approx(100.0)
    assert result.formula_trace["period_start"] == "2026-01-01"
