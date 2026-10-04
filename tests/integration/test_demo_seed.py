from __future__ import annotations

import csv
import hashlib
import io

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.db.models.agent import Agent
from app.db.models.analytics import Anomaly
from app.db.models.dataset import DataSource, IngestionJob, StagedRow
from app.db.models.semantic import SemanticMapping, TenantIndustryPack
from app.db.session import SessionLocal
from app.services.analytics.customer_behavior import analyze_tenant_customer_behavior
from app.services.analytics.forecasting import run_forecast_for_concept
from app.services.analytics.kpi import evaluate_kpi
from app.workers.tasks.ingestion import run_ingestion_once
from scripts.seed_demo import (
    DEMO_SOURCE_NAME,
    build_demo_datasets,
    seed_demo_data,
    write_demo_files,
)


@pytest.mark.asyncio
async def test_meridian_demo_prepares_files_without_ingesting_them(
    tmp_path,
    client: AsyncClient,
    two_tenants: dict,
) -> None:
    files_dir = write_demo_files(tmp_path)
    files = sorted(files_dir.glob("*.csv"))
    assert len(files) == 6

    row_count = 0
    for file_path in files:
        with file_path.open(encoding="utf-8", newline="") as file:
            rows = list(csv.DictReader(file))
        row_count += len(rows)
        assert rows
    assert row_count == 148

    datasets = {dataset.key: dataset for dataset in build_demo_datasets()}
    with (files_dir / "fuel-records.csv").open(
        encoding="utf-8", newline=""
    ) as file:
        fuel_rows = list(csv.DictReader(file))
    assert float(fuel_rows[-1]["Fuel Use Rate"]) == 11.8
    assert float(fuel_rows[-1]["Fuel Consumed"]) > float(
        fuel_rows[0]["Fuel Consumed"]
    )
    assert datasets["customer-transactions"].date_column == "Transaction Day"

    async with SessionLocal() as db:
        legacy_source = DataSource(
            tenant_id=two_tenants["tenant_a"],
            name="Old generated fuel data",
            source_type="csv",
            config={"demo_seed_key": "fuel-records"},
        )
        db.add(legacy_source)
        await db.flush()
        legacy_job = IngestionJob(
            tenant_id=two_tenants["tenant_a"],
            source_id=legacy_source.id,
            status="succeeded",
            rows_staged=1,
        )
        db.add(legacy_job)
        await db.flush()
        legacy_row = StagedRow(
            tenant_id=two_tenants["tenant_a"],
            source_id=legacy_source.id,
            job_id=legacy_job.id,
            row_number=1,
            raw_data={"Fuel Use Rate": "8.2"},
            search_text='{"Fuel Use Rate": "8.2"}',
        )
        db.add(legacy_row)
        await db.flush()

        first = await seed_demo_data(
            db,
            tenant_id=two_tenants["tenant_a"],
            admin_user_id=two_tenants["user_a"],
        )
        await db.commit()
        second = await seed_demo_data(
            db,
            tenant_id=two_tenants["tenant_a"],
            admin_user_id=two_tenants["user_a"],
        )
        await db.commit()

        assert first == {
            "sources": 1,
            "rows": 0,
            "files": 6,
            "legacy_archived": 1,
        }
        assert second == {
            "sources": 1,
            "rows": 0,
            "files": 6,
            "legacy_archived": 0,
        }

        sources = list(
            (
                await db.execute(
                    select(DataSource).where(
                        DataSource.tenant_id == two_tenants["tenant_a"]
                    )
                )
            ).scalars().all()
        )
        assert len(sources) == 2
        source = next(source for source in sources if source.name == DEMO_SOURCE_NAME)
        assert source.name == DEMO_SOURCE_NAME
        assert source.source_type == "agent"
        assert source.config["demo_profile"] == "meridian-transport"
        assert source.config["content_date_columns"]["daily-operations.csv"] == (
            "Service Date"
        )

        mappings = list(
            (
                await db.execute(
                    select(SemanticMapping).where(
                        SemanticMapping.tenant_id == two_tenants["tenant_a"],
                        SemanticMapping.source_id == source.id,
                    )
                )
            ).scalars().all()
        )
        mapping_by_column = {mapping.source_column: mapping for mapping in mappings}
        assert mapping_by_column["Fuel Use Rate"].canonical_concept_key == (
            "Transport.FuelUseRate"
        )
        assert mapping_by_column["Fuel Use Rate"].status == "confirmed"
        assert mapping_by_column["Revenue"].canonical_concept_key == "Finance.Revenue"
        assert mapping_by_column["Revenue"].status == "confirmed"

        assert not (
            await db.execute(
                select(StagedRow.id).where(
                    StagedRow.tenant_id == two_tenants["tenant_a"],
                    StagedRow.source_id == source.id,
                )
            )
        ).scalars().all()
        assert (
            await db.execute(
                select(IngestionJob.id).where(
                    IngestionJob.tenant_id == two_tenants["tenant_a"],
                    IngestionJob.source_id == source.id,
                )
            )
        ).scalars().all() == []
        assert legacy_source.is_active is False
        assert legacy_row.raw_data == {"Fuel Use Rate": "8.2"}
        assert not (
            await db.execute(
                select(Agent.id).where(
                    Agent.tenant_id == two_tenants["tenant_a"]
                )
            )
        ).scalars().all()

        packs = (
            await db.execute(
                select(TenantIndustryPack).where(
                    TenantIndustryPack.tenant_id == two_tenants["tenant_a"]
                )
            )
        ).scalars().all()
        assert {pack.pack_key for pack in packs} == {
            "procurement.v1",
            "transport.v1",
        }

    login = await client.post(
        "/api/v1/auth/login",
        json={
            "tenant_slug": two_tenants["tenant_slug_a"],
            "email": two_tenants["email_a"],
            "password": two_tenants["password"],
        },
    )
    assert login.status_code == 200
    overview = await client.get("/")
    assert overview.status_code == 200
    assert "From source file to business signal" in overview.text
    assert "Waiting for watcher enrollment" in overview.text
    assert "Synthetic demo records" in overview.text

    source = next(source for source in sources if source.name == DEMO_SOURCE_NAME)
    csrf_token = client.cookies.get("sansa_csrf")
    write_headers = {"X-CSRF-Token": csrf_token} if csrf_token else {}
    enrollment = await client.post(
        "/api/v1/agents/enroll",
        json={"name": "Meridian File Watcher", "source_id": str(source.id)},
        headers=write_headers,
    )
    assert enrollment.status_code == 201, enrollment.text
    agent_id = enrollment.json()["agent_id"]
    registration = await client.post(
        "/api/v1/agents/register",
        json={"enrollment_token": enrollment.json()["enrollment_token"]},
    )
    assert registration.status_code == 200, registration.text

    observed_files = []
    for file_path in files:
        content = file_path.read_bytes()
        observed_files.append({
            "path": file_path.name,
            "content": content,
            "content_hash": hashlib.sha256(content).hexdigest(),
            "byte_size": len(content),
        })
    sync = await client.post(
        f"/api/v1/agents/{agent_id}/sync",
        json={
            "files": [
                {
                    "path": file["path"],
                    "content_hash": file["content_hash"],
                    "byte_size": file["byte_size"],
                    "status": "new",
                }
                for file in observed_files
            ]
        },
        headers={"Authorization": f"Bearer {registration.json()['credential']}"},
    )
    assert sync.status_code == 200, sync.text

    overview = await client.get("/")
    assert "Ask watcher to upload 6 files" in overview.text
    assert "daily-operations.csv" in overview.text
    assert observed_files[0]["content_hash"][:12] in overview.text

    ingest = await client.post(
        f"/api/v1/datasets/{source.id}/ingest",
        headers=write_headers,
    )
    assert ingest.status_code == 202, ingest.text
    assert ingest.json()["agent_job_count"] == 6
    queue_overview = await client.get("/")
    assert "0/6 ingestion jobs complete" in queue_overview.text

    agent_headers = {
        "Authorization": f"Bearer {registration.json()['credential']}"
    }
    jobs = await client.get(f"/api/v1/agents/{agent_id}/jobs", headers=agent_headers)
    assert jobs.status_code == 200, jobs.text
    upload_jobs = jobs.json()
    assert len(upload_jobs) == 6
    observed_by_path = {file["path"]: file for file in observed_files}
    for job in upload_jobs:
        path = job["params"]["path"]
        source_file = observed_by_path[path]
        upload = await client.post(
            f"/api/v1/agents/{agent_id}/upload/{source_file['content_hash']}",
            files={
                "file": (
                    path,
                    io.BytesIO(source_file["content"]),
                    "text/csv",
                )
            },
            headers=agent_headers,
        )
        assert upload.status_code == 201, upload.text
        result = await client.post(
            f"/api/v1/agents/{agent_id}/jobs/{job['id']}/result",
            json={
                "status": "completed",
                "result": {"actual_hash": source_file["content_hash"]},
            },
            headers=agent_headers,
        )
        assert result.status_code == 200, result.text

    while True:
        async with SessionLocal() as db:
            processed = await run_ingestion_once(db)
            await db.commit()
        if not processed:
            break

    async with SessionLocal() as db:
        ingestion_jobs = (
            await db.execute(
                select(IngestionJob)
                .where(IngestionJob.source_id == source.id)
                .order_by(IngestionJob.created_at)
            )
        ).scalars().all()
        assert len(ingestion_jobs) == 6
        assert all(job.status == "succeeded" for job in ingestion_jobs)

        staged_rows = (
            await db.execute(
                select(StagedRow).where(StagedRow.source_id == source.id)
            )
        ).scalars().all()
        assert len(staged_rows) == 148

        revenue = await evaluate_kpi(
            db,
            key="sales.total_revenue",
            tenant_id=two_tenants["tenant_a"],
            source_ids=[source.id],
        )
        assert revenue.value == 209_310

        passengers = await evaluate_kpi(
            db,
            key="transport.passenger_volume",
            tenant_id=two_tenants["tenant_a"],
            source_ids=[source.id],
        )
        assert passengers.value == 12_000

        customer_analysis = await analyze_tenant_customer_behavior(
            db,
            tenant_id=two_tenants["tenant_a"],
            source_ids=[source.id],
        )
        assert customer_analysis is not None
        assert customer_analysis.customer_count == 7
        bluebird = next(
            profile for profile in customer_analysis.customers
            if profile.customer_key == "Bluebird Travel"
        )
        assert bluebird.segment == "declining"
        assert bluebird.trend_pct == pytest.approx(-66.6667, rel=1e-4)

        forecasts = await run_forecast_for_concept(
            db,
            tenant_id=two_tenants["tenant_a"],
            value_concept="Finance.Cost",
            group_by_concept=None,
            horizon=4,
            source_ids=[source.id],
        )
        assert len(forecasts) == 1
        assert forecasts[0].status == "ok"
        assert forecasts[0].source_ids == [str(source.id)]

        anomalies = (
            await db.execute(
                select(Anomaly).where(
                    Anomaly.tenant_id == two_tenants["tenant_a"]
                )
            )
        ).scalars().all()
        assert {
            anomaly.detector_key for anomaly in anomalies
        } >= {
            "operations.downtime_spikes",
            "transport.fuel_use_rate_spikes",
            "transport.supplier_delay_spikes",
        }
        assert max(
            anomaly.value for anomaly in anomalies
            if anomaly.detector_key == "operations.downtime_spikes"
        ) == 18.0

        await db.commit()
