"""
Create a ready-to-explore Meridian Transport demo tenant.

Usage (PowerShell):
    $env:DEMO_ADMIN_EMAIL = "demo@example.test"
    $env:DEMO_ADMIN_PASSWORD = "<choose-a-strong-password>"
    uv run python -m scripts.seed_demo
    Remove-Item Env:DEMO_ADMIN_EMAIL, Env:DEMO_ADMIN_PASSWORD

The tenant and its demo data are reconciled idempotently. The password is
never stored in source control; an existing demo user's password is not reset.
"""
from __future__ import annotations

import asyncio
import csv
import io
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.db.models.analytics import Forecast
from app.db.models.dataset import DataSource, IngestionJob, StagedRow
from app.db.models.semantic import SemanticMapping
from app.db.models.tenant import Tenant
from app.db.models.user import User
from app.db.seed import ensure_permission_catalog, seed_tenant_roles
from app.db.seed_anomaly_detectors import seed_anomaly_detectors
from app.db.seed_concepts import seed_canonical_concepts
from app.db.seed_kpis import seed_kpis
from app.db.seed_packs import seed_industry_packs
from app.db.session import SessionLocal
from app.services.analytics.anomalies import run_detector
from app.services.analytics.forecasting import run_forecast_for_concept
from app.services.ingestion.service import build_storage_key, run_job
from app.services.semantic import packs as packs_service
from app.services.storage.local import storage
from app.services.timestamps.service import record_row_timestamp
from app.services.understanding.mapper import propose_mappings

TENANT_NAME = "Meridian Transport"
TENANT_SLUG = "meridian-transport-demo"
SYSTEM_EMAIL_TEMPLATE = "system+{tenant_id}@sansa.local"


@dataclass(frozen=True)
class DemoDataset:
    key: str
    name: str
    date_column: str
    columns: tuple[str, ...]
    rows: list[dict[str, str]]
    mappings: dict[str, str]


def _weekly_dates() -> list[date]:
    today = date.today()
    first = today - timedelta(weeks=11)
    return [first + timedelta(weeks=week) for week in range(12)]


def build_demo_datasets() -> list[DemoDataset]:
    """Create deterministic, synthetic business data with rolling dates."""
    weeks = _weekly_dates()

    operations = []
    tickets = []
    fuel = []
    suppliers = []
    routes = []

    for index, service_date in enumerate(weeks):
        iso_date = service_date.isoformat()
        downtime = 18.0 if index == len(weeks) - 1 else 1.2 + (index % 3) * 0.15
        operations.append({
            "Service Date": iso_date,
            "Depot": "Central Depot",
            "Capacity": "560",
            "Occupied Hours": str(458 + index % 4),
            "Downtime": f"{downtime:.2f}",
            "Operating Cost": f"{260 * (1.075 ** index):.2f}",
        })

        for route, passengers, revenue in (
            ("Airport Express", 310, 5200),
            ("Harbor Line", 420, 6800),
            ("University Loop", 270, 4000),
        ):
            tickets.append({
                "Ticket Date": iso_date,
                "Ticket Ref": f"T-{index + 1:02d}-{len(tickets) + 1:03d}",
                "Route": route,
                "Passengers": str(passengers),
                "Revenue": str(revenue),
            })

        fuel_rate = 8.2 + index * 0.025
        if index == len(weeks) - 1:
            fuel_rate = 11.8
        distance_km = 1800
        litres = distance_km * fuel_rate / 100
        fuel.append({
            "Service Date": iso_date,
            "Vehicle": "Bus-17",
            "Route": "Airport Express",
            "Distance KM": str(distance_km),
            "Fuel Consumed": f"{litres:.2f}",
            "Fuel Use Rate": f"{fuel_rate:.2f}",
            "Cost": f"{litres * 1.65:.2f}",
        })

        lead_time = 3.0 + index * 0.08
        if index == len(weeks) - 1:
            lead_time = 18.0
        suppliers.append({
            "Received Date": iso_date,
            "Supplier": "Gulf Fleet Parts",
            "Lead Time": f"{lead_time:.2f}",
            "Buy Price": f"{1850 + (index % 3) * 25:.2f}",
        })

        for route_name, distance, travel_minutes in (
            ("Airport Express", 42, 64 + index % 3),
            ("Harbor Line", 31, 51 + index % 4),
            ("University Loop", 24, 39 + index % 2),
        ):
            routes.append({
                "Trip Date": iso_date,
                "Route": route_name,
                "Distance KM": str(distance),
                "Travel Minutes": str(travel_minutes),
            })

    return [
        DemoDataset(
            key="daily-operations",
            name="Daily Operations",
            date_column="Service Date",
            columns=(
                "Service Date", "Depot", "Capacity", "Occupied Hours",
                "Downtime", "Operating Cost",
            ),
            rows=operations,
            mappings={
                "Depot": "Management.BusinessUnit",
                "Capacity": "Operations.Capacity",
                "Occupied Hours": "Operations.OccupiedHours",
                "Downtime": "Operations.Downtime",
                "Operating Cost": "Finance.Cost",
            },
        ),
        DemoDataset(
            key="ticket-transactions",
            name="Ticket Transactions",
            date_column="Ticket Date",
            columns=("Ticket Date", "Ticket Ref", "Route", "Passengers", "Revenue"),
            rows=tickets,
            mappings={
                "Route": "Transport.Route",
                "Passengers": "Transport.PassengerCount",
                "Revenue": "Finance.Revenue",
            },
        ),
        DemoDataset(
            key="fuel-records",
            name="Fuel Records",
            date_column="Service Date",
            columns=(
                "Service Date", "Vehicle", "Route", "Distance KM",
                "Fuel Consumed", "Fuel Use Rate", "Cost",
            ),
            rows=fuel,
            mappings={
                "Vehicle": "Transport.Vehicle",
                "Route": "Transport.Route",
                "Distance KM": "Transport.DistanceKm",
                "Fuel Consumed": "Transport.FuelConsumed",
                "Fuel Use Rate": "Transport.FuelUseRate",
            },
        ),
        DemoDataset(
            key="supplier-records",
            name="Supplier Records",
            date_column="Received Date",
            columns=("Received Date", "Supplier", "Lead Time", "Buy Price"),
            rows=suppliers,
            mappings={
                "Supplier": "Procurement.Supplier",
                "Lead Time": "Procurement.LeadTime",
                "Buy Price": "Procurement.PurchasePrice",
            },
        ),
        DemoDataset(
            key="route-performance",
            name="Route Performance",
            date_column="Trip Date",
            columns=("Trip Date", "Route", "Distance KM", "Travel Minutes"),
            rows=routes,
            mappings={
                "Route": "Transport.Route",
                "Distance KM": "Transport.DistanceKm",
                "Travel Minutes": "Transport.TravelMinutes",
            },
        ),
    ]


def _csv_bytes(dataset: DemoDataset) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=dataset.columns)
    writer.writeheader()
    writer.writerows(dataset.rows)
    return output.getvalue().encode("utf-8")


async def _ensure_dataset(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    admin_user_id: uuid.UUID,
    dataset: DemoDataset,
) -> tuple[DataSource, IngestionJob]:
    source = (
        await db.execute(
            select(DataSource).where(
                DataSource.tenant_id == tenant_id,
                DataSource.name == dataset.name,
            )
        )
    ).scalar_one_or_none()
    if source is None:
        source = DataSource(
            tenant_id=tenant_id,
            name=dataset.name,
            source_type="csv",
            config={"demo_seed_key": dataset.key},
        )
        db.add(source)
        await db.flush()
    elif source.config.get("demo_seed_key") != dataset.key:
        raise ValueError(
            f"cannot seed demo dataset {dataset.name!r}: a different source "
            "already uses that name"
        )

    job = (
        await db.execute(
            select(IngestionJob)
            .where(
                IngestionJob.tenant_id == tenant_id,
                IngestionJob.source_id == source.id,
                IngestionJob.status == "succeeded",
            )
            .order_by(IngestionJob.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if job is None:
        filename = f"{dataset.key}.csv"
        key = build_storage_key(
            tenant_id=tenant_id,
            source_id=source.id,
            extension=".csv",
        )
        await storage.put(key=key, content=_csv_bytes(dataset))
        source.config = {
            **source.config,
            "storage_key": key,
            "original_filename": filename,
        }
        job = IngestionJob(
            tenant_id=tenant_id,
            source_id=source.id,
            status="pending",
        )
        db.add(job)
        await db.flush()
        await run_job(db, job_id=job.id)
        await db.refresh(job)
        if job.status != "succeeded":
            raise RuntimeError(
                f"demo ingestion failed for {dataset.name}: "
                f"{job.error_message or job.status}"
            )

    staged_rows = list(
        (
            await db.execute(
                select(StagedRow)
                .where(
                    StagedRow.tenant_id == tenant_id,
                    StagedRow.job_id == job.id,
                )
                .order_by(StagedRow.row_number)
            )
        ).scalars().all()
    )
    await db.flush()
    for row in staged_rows:
        timestamp_value = row.raw_data.get(dataset.date_column)
        if not timestamp_value:
            raise ValueError(
                f"demo row {row.row_number} in {dataset.name} has no "
                f"{dataset.date_column!r}"
            )
        timestamp = datetime.combine(
            date.fromisoformat(str(timestamp_value)),
            time.min,
            tzinfo=UTC,
        )
        await record_row_timestamp(
            db,
            tenant_id=tenant_id,
            staged_row_id=row.id,
            kind="content",
            timestamp=timestamp,
            context=dataset.date_column,
        )

    await propose_mappings(
        db,
        tenant_id=tenant_id,
        source_id=source.id,
        job_id=job.id,
    )
    mappings = list(
        (
            await db.execute(
                select(SemanticMapping).where(
                    SemanticMapping.tenant_id == tenant_id,
                    SemanticMapping.source_id == source.id,
                )
            )
        ).scalars().all()
    )
    mappings_by_column = {mapping.source_column: mapping for mapping in mappings}
    for column, concept_key in dataset.mappings.items():
        mapping = mappings_by_column.get(column)
        if mapping is None or mapping.canonical_concept_key != concept_key:
            actual = mapping.canonical_concept_key if mapping else "unmapped"
            raise ValueError(
                f"demo semantic mapping mismatch for {dataset.name}.{column}: "
                f"expected {concept_key}, got {actual}"
            )
        if mapping.status == "rejected":
            raise ValueError(
                f"demo mapping {dataset.name}.{column} was rejected; "
                "review it before rerunning the seed"
            )
        if mapping.status != "confirmed":
            mapping.status = "confirmed"
            mapping.confirmed_by_user_id = admin_user_id
            mapping.confirmed_at = datetime.now(UTC)

    await db.flush()
    return source, job


async def seed_demo_data(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    admin_user_id: uuid.UUID,
) -> dict[str, int]:
    """Seed the tenant's semantic catalog, demo sources, and real analytics."""
    admin_exists = (
        await db.execute(
            select(User.id).where(
                User.id == admin_user_id,
                User.tenant_id == tenant_id,
                User.is_system.is_(False),
                User.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if admin_exists is None:
        raise ValueError("active demo administrator does not belong to the tenant")

    await seed_canonical_concepts(db)
    await seed_industry_packs(db)
    await seed_kpis(db)
    await seed_anomaly_detectors(db)
    await db.flush()

    await packs_service.install_pack(
        db, tenant_id=tenant_id, pack_key="procurement.v1"
    )
    await packs_service.install_pack(
        db, tenant_id=tenant_id, pack_key="transport.v1"
    )

    sources: dict[str, uuid.UUID] = {}
    total_rows = 0
    for dataset in build_demo_datasets():
        source, job = await _ensure_dataset(
            db,
            tenant_id=tenant_id,
            admin_user_id=admin_user_id,
            dataset=dataset,
        )
        sources[dataset.key] = source.id
        total_rows += job.rows_staged

    anomaly_count = 0
    for detector_key in (
        "operations.downtime_spikes",
        "transport.fuel_use_rate_spikes",
        "transport.supplier_delay_spikes",
    ):
        anomalies = await run_detector(
            db,
            detector_key=detector_key,
            tenant_id=tenant_id,
            source_ids=list(sources.values()),
        )
        anomaly_count += len(anomalies)

    existing_forecast = (
        await db.execute(
            select(Forecast.id).where(
                Forecast.tenant_id == tenant_id,
                Forecast.value_concept == "Finance.Cost",
                Forecast.status == "ok",
            ).limit(1)
        )
    ).scalar_one_or_none()
    if existing_forecast is None:
        forecasts = await run_forecast_for_concept(
            db,
            tenant_id=tenant_id,
            value_concept="Finance.Cost",
            group_by_concept=None,
            horizon=4,
            source_ids=[sources["daily-operations"]],
        )
        if not forecasts or any(result.status != "ok" for result in forecasts):
            raise RuntimeError(
                "demo operating-cost forecast could not be generated from "
                "the seeded weekly operating-cost history"
            )

    await db.flush()
    return {
        "sources": len(sources),
        "rows": total_rows,
        "new_anomalies": anomaly_count,
    }


async def _ensure_demo_access(
    db: AsyncSession,
    *,
    email: str,
    password: str,
) -> tuple[Tenant, User]:
    tenant = (
        await db.execute(
            select(Tenant).where(Tenant.slug == TENANT_SLUG)
        )
    ).scalar_one_or_none()
    if tenant is None:
        tenant = Tenant(name=TENANT_NAME, slug=TENANT_SLUG)
        db.add(tenant)
        await db.flush()
    elif tenant.name != TENANT_NAME:
        raise ValueError(
            f"tenant slug {TENANT_SLUG!r} already belongs to {tenant.name!r}"
        )

    await ensure_permission_catalog(db)
    roles = await seed_tenant_roles(db, tenant_id=tenant.id)
    user = (
        await db.execute(
            select(User).where(
                User.tenant_id == tenant.id,
                User.email == email,
                User.is_system.is_(False),
            )
        )
    ).scalar_one_or_none()
    if user is None:
        user = User(
            tenant_id=tenant.id,
            email=email,
            password_hash=hash_password(password),
            full_name="Meridian Demo Administrator",
            is_active=True,
        )
        user.roles = [roles["admin"]]
        db.add(user)
        await db.flush()
    elif not verify_password(password, user.password_hash):
        raise ValueError(
            "DEMO_ADMIN_PASSWORD does not match the existing demo user's "
            "password; it was not changed"
        )
    elif not user.is_active:
        raise ValueError("the existing Meridian demo user is inactive")
    else:
        user.roles = [roles["admin"]]

    system_user = (
        await db.execute(
            select(User).where(
                User.tenant_id == tenant.id,
                User.is_system.is_(True),
            )
        )
    ).scalar_one_or_none()
    if system_user is None:
        db.add(User(
            tenant_id=tenant.id,
            email=SYSTEM_EMAIL_TEMPLATE.format(tenant_id=tenant.id),
            password_hash=hash_password(secrets.token_urlsafe(48)),
            full_name="System",
            is_active=True,
            is_system=True,
        ))

    await db.flush()
    return tenant, user


async def run(*, email: str, password: str) -> None:
    async with SessionLocal() as db:
        tenant, user = await _ensure_demo_access(
            db, email=email, password=password
        )
        await db.commit()
        counts = await seed_demo_data(
            db,
            tenant_id=tenant.id,
            admin_user_id=user.id,
        )
        await db.commit()
        print(
            f"Seeded {TENANT_NAME}: {counts['sources']} sources, "
            f"{counts['rows']} staged rows, "
            f"{counts['new_anomalies']} new anomaly signals."
        )
        print(f"Tenant slug: {TENANT_SLUG}")
        print(f"Admin email: {email}")


def main() -> None:
    email = os.environ.get("DEMO_ADMIN_EMAIL", "").strip()
    password = os.environ.get("DEMO_ADMIN_PASSWORD", "")
    if not email or not password:
        raise SystemExit(
            "Set DEMO_ADMIN_EMAIL and DEMO_ADMIN_PASSWORD before running "
            "the demo seed script."
        )
    asyncio.run(run(email=email, password=password))


if __name__ == "__main__":
    main()
