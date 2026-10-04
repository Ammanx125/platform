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
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.db.models.dataset import DataSource
from app.db.models.semantic import SemanticMapping
from app.db.models.tenant import Tenant
from app.db.models.user import User
from app.db.seed import ensure_permission_catalog, seed_tenant_roles
from app.db.seed_anomaly_detectors import seed_anomaly_detectors
from app.db.seed_concepts import seed_canonical_concepts
from app.db.seed_kpis import seed_kpis
from app.db.seed_packs import seed_industry_packs
from app.db.session import SessionLocal
from app.services.semantic import packs as packs_service

TENANT_NAME = "Meridian Transport"
TENANT_SLUG = "meridian-transport-demo"
DEMO_SOURCE_NAME = "Meridian Watcher Files"
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
    customer_transactions = []

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

    customer_orders = {
        "Meridian Priority": [(age, 450) for age in range(7, 169, 14)],
        "Harbor Tours": [(age, 210) for age in (8, 38, 68, 98, 128, 158)],
        "Coastal Excursions": [
            (10, 450), (40, 300), (70, 100), (100, 100), (130, 100), (160, 100),
        ],
        "Bluebird Travel": [
            (10, 500), (40, 1500), (70, 1200), (100, 1000), (130, 900), (160, 800),
        ],
        "Desert Link": [(100, 400), (130, 450), (160, 500), (200, 600)],
        "Old Town Shuttle": [(210, 400), (240, 400), (270, 400), (300, 400)],
        "QuickRide Local": [(7, 25), (21, 25)],
    }
    today = date.today()
    transaction_number = 0
    for customer, orders in customer_orders.items():
        for days_ago, amount in orders:
            transaction_number += 1
            customer_transactions.append({
                "Customer Name": customer,
                "Transaction ID": f"MT-{transaction_number:04d}",
                "Transaction Day": (today - timedelta(days=days_ago)).isoformat(),
                "Revenue": f"{amount:.2f}",
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
        DemoDataset(
            key="customer-transactions",
            name="Customer Transactions",
            date_column="Transaction Day",
            columns=(
                "Customer Name", "Transaction ID", "Transaction Day", "Revenue",
            ),
            rows=customer_transactions,
            mappings={
                "Customer Name": "Sales.Customer",
                "Transaction ID": "Sales.TransactionId",
                "Transaction Day": "Sales.TransactionDate",
                "Revenue": "Finance.Revenue",
            },
        ),
    ]


def _csv_bytes(dataset: DemoDataset) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=dataset.columns)
    writer.writeheader()
    writer.writerows(dataset.rows)
    return output.getvalue().encode("utf-8")


def write_demo_files(directory: Path | None = None) -> Path:
    """Write the watcher's actual, Excel-openable source files."""
    output_dir = directory or (
        Path(__file__).resolve().parents[1]
        / "data"
        / "demo"
        / "meridian_transport"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    for dataset in build_demo_datasets():
        (output_dir / f"{dataset.key}.csv").write_bytes(_csv_bytes(dataset))
    return output_dir


async def _ensure_agent_source(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    admin_user_id: uuid.UUID,
) -> DataSource:
    datasets = build_demo_datasets()
    source = (
        await db.execute(
            select(DataSource).where(
                DataSource.tenant_id == tenant_id,
                DataSource.name == DEMO_SOURCE_NAME,
            )
        )
    ).scalar_one_or_none()
    content_date_columns = {
        f"{dataset.key}.csv": dataset.date_column for dataset in datasets
    }
    demo_files = [f"{dataset.key}.csv" for dataset in datasets]
    config = {
        "demo_profile": "meridian-transport",
        "demo_files": demo_files,
        "content_date_columns": content_date_columns,
    }
    if source is None:
        source = DataSource(
            tenant_id=tenant_id,
            name=DEMO_SOURCE_NAME,
            source_type="agent",
            config=config,
        )
        db.add(source)
        await db.flush()
    elif source.source_type != "agent":
        raise ValueError(
            f"cannot prepare {DEMO_SOURCE_NAME!r}: an existing source with "
            f"that name has type {source.source_type!r}, expected 'agent'"
        )
    else:
        source.config = {**source.config, **config}
        source.is_active = True

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
    expected_mappings: dict[str, str] = {}
    for dataset in datasets:
        for column, concept_key in dataset.mappings.items():
            previous = expected_mappings.setdefault(column, concept_key)
            if previous != concept_key:
                raise ValueError(
                    f"demo data dictionary maps {column!r} to both "
                    f"{previous!r} and {concept_key!r}"
                )

    for column, concept_key in expected_mappings.items():
        mapping = mappings_by_column.get(column)
        if mapping is None:
            mapping = SemanticMapping(
                tenant_id=tenant_id,
                source_id=source.id,
                source_column=column,
                canonical_concept_key=concept_key,
                status="confirmed",
                confidence=None,
                rationale={"method": "demo_data_dictionary"},
                confirmed_by_user_id=admin_user_id,
                confirmed_at=datetime.now(UTC),
            )
            db.add(mapping)
            continue
        if mapping.canonical_concept_key != concept_key:
            raise ValueError(
                f"demo semantic mapping mismatch for {DEMO_SOURCE_NAME}.{column}: "
                f"expected {concept_key}, got {mapping.canonical_concept_key}"
            )
        if mapping.status == "rejected":
            raise ValueError(
                f"demo mapping {column!r} was rejected; review it before "
                "preparing the demo"
            )
        if mapping.status != "confirmed":
            mapping.status = "confirmed"
            mapping.confirmed_by_user_id = admin_user_id
            mapping.confirmed_at = datetime.now(UTC)

    await db.flush()
    return source


async def _archive_legacy_seed_sources(
    db: AsyncSession, *, tenant_id: uuid.UUID
) -> int:
    sources = (
        await db.execute(
            select(DataSource).where(DataSource.tenant_id == tenant_id)
        )
    ).scalars().all()
    archived = 0
    for source in sources:
        if source.config.get("demo_seed_key") and source.is_active:
            source.is_active = False
            archived += 1
    return archived


async def seed_demo_data(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    admin_user_id: uuid.UUID,
) -> dict[str, int]:
    """Prepare tenant catalogs and source metadata without ingesting file rows."""
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

    await _ensure_agent_source(
        db, tenant_id=tenant_id, admin_user_id=admin_user_id
    )
    legacy_archived = await _archive_legacy_seed_sources(
        db, tenant_id=tenant_id
    )
    await db.flush()
    return {
        "sources": 1,
        "rows": 0,
        "files": len(build_demo_datasets()),
        "legacy_archived": legacy_archived,
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
            full_name="Meridian Transport Manager",
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
        if user.full_name in (None, "Meridian Demo Administrator"):
            user.full_name = "Meridian Transport Manager"

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
    files_dir = write_demo_files()
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
            f"Prepared {counts['files']} CSV files at {files_dir} and "
            f"provisioned {counts['sources']} agent source for {TENANT_NAME}. "
            f"No business rows were ingested; archived "
            f"{counts['legacy_archived']} legacy seed source(s) without "
            "deleting their history."
        )
        print("Open the CSV files in Excel, then point the watcher at this folder.")
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
