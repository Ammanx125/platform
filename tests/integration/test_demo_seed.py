from __future__ import annotations

from collections import defaultdict

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.db.models.analytics import Anomaly, Forecast
from app.db.models.dataset import DataSource, StagedRow
from app.db.models.semantic import SemanticMapping, TenantIndustryPack
from app.db.models.understanding import DataProfile
from app.db.session import SessionLocal
from app.services.analytics.kpi import evaluate_kpi
from app.services.storage.local import storage
from scripts.seed_demo import seed_demo_data


@pytest.mark.asyncio
async def test_meridian_demo_seed_is_semantic_and_idempotent(
    monkeypatch: pytest.MonkeyPatch,
    client: AsyncClient,
    two_tenants: dict,
) -> None:
    stored_files: dict[str, bytes] = {}

    async def fake_put(*, key: str, content: bytes) -> None:
        stored_files[key] = content

    async def fake_get(*, key: str) -> bytes:
        return stored_files[key]

    monkeypatch.setattr(storage, "put", fake_put)
    monkeypatch.setattr(storage, "get", fake_get)

    async with SessionLocal() as db:
        counts = await seed_demo_data(
            db,
            tenant_id=two_tenants["tenant_a"],
            admin_user_id=two_tenants["user_a"],
        )
        await db.commit()

        assert counts["sources"] == 5
        assert counts["rows"] == 108
        assert len(stored_files) == 5

        sources = list(
            (
                await db.execute(
                    select(DataSource).where(
                        DataSource.tenant_id == two_tenants["tenant_a"]
                    )
                )
            ).scalars().all()
        )
        assert {source.name for source in sources} == {
            "Daily Operations",
            "Ticket Transactions",
            "Fuel Records",
            "Supplier Records",
            "Route Performance",
        }

        fuel_source = next(source for source in sources if source.name == "Fuel Records")
        fuel_row = (
            await db.execute(
                select(StagedRow)
                .where(StagedRow.source_id == fuel_source.id)
                .order_by(StagedRow.row_number)
                .limit(1)
            )
        ).scalar_one()
        assert "Fuel Use Rate" in fuel_row.raw_data
        assert "Transport.FuelUseRate" not in fuel_row.raw_data
        fuel_rows = (
            await db.execute(
                select(StagedRow)
                .where(StagedRow.source_id == fuel_source.id)
                .order_by(StagedRow.row_number)
            )
        ).scalars().all()
        assert float(fuel_rows[-1].raw_data["Fuel Use Rate"]) > float(
            fuel_rows[0].raw_data["Fuel Use Rate"]
        )
        assert float(fuel_rows[-1].raw_data["Fuel Consumed"]) > float(
            fuel_rows[0].raw_data["Fuel Consumed"]
        )
        operations_source = next(
            source for source in sources if source.name == "Daily Operations"
        )
        operations_rows = (
            await db.execute(
                select(StagedRow)
                .where(StagedRow.source_id == operations_source.id)
                .order_by(StagedRow.row_number)
            )
        ).scalars().all()

        fuel_mapping = (
            await db.execute(
                select(SemanticMapping).where(
                    SemanticMapping.tenant_id == two_tenants["tenant_a"],
                    SemanticMapping.source_id == fuel_source.id,
                    SemanticMapping.source_column == "Fuel Use Rate",
                )
            )
        ).scalar_one()
        assert fuel_mapping.canonical_concept_key == "Transport.FuelUseRate"
        assert fuel_mapping.status == "confirmed"

        profiles = (
            await db.execute(
                select(DataProfile).where(
                    DataProfile.tenant_id == two_tenants["tenant_a"]
                )
            )
        ).scalars().all()
        assert len(profiles) == 5

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

        passenger_kpi = await evaluate_kpi(
            db,
            key="transport.passenger_volume",
            tenant_id=two_tenants["tenant_a"],
        )
        assert passenger_kpi.value == 12_000

        ticket_source = next(
            source for source in sources if source.name == "Ticket Transactions"
        )
        ticket_rows = (
            await db.execute(
                select(StagedRow).where(StagedRow.source_id == ticket_source.id)
            )
        ).scalars().all()
        revenue_by_week: dict[str, float] = defaultdict(float)
        for row in ticket_rows:
            revenue_by_week[row.raw_data["Ticket Date"]] += float(
                row.raw_data["Revenue"]
            )
        assert len(set(revenue_by_week.values())) == 1

        revenue_kpi = await evaluate_kpi(
            db,
            key="sales.total_revenue",
            tenant_id=two_tenants["tenant_a"],
        )
        assert revenue_kpi.value == 192_000

        forecast_rows = (
            await db.execute(
                select(Forecast).where(
                    Forecast.tenant_id == two_tenants["tenant_a"],
                    Forecast.value_concept == "Finance.Cost",
                    Forecast.status == "ok",
                )
            )
        ).scalars().all()
        assert len(forecast_rows) == 1
        assert len(forecast_rows[0].predicted_points) == 4
        forecast_values = [
            point["value"] for point in forecast_rows[0].predicted_points
        ]
        assert forecast_values == sorted(forecast_values)
        assert (
            forecast_values[-1]
            > float(operations_rows[-1].raw_data["Operating Cost"])
        )

        anomaly_rows = (
            await db.execute(
                select(Anomaly).where(
                    Anomaly.tenant_id == two_tenants["tenant_a"]
                )
            )
        ).scalars().all()
        assert {
            row.detector_key for row in anomaly_rows
        } == {
            "operations.downtime_spikes",
            "transport.fuel_use_rate_spikes",
            "transport.supplier_delay_spikes",
        }
        anomaly_values = {
            key: max(row.value for row in anomaly_rows if row.detector_key == key)
            for key in {
                "operations.downtime_spikes",
                "transport.fuel_use_rate_spikes",
                "transport.supplier_delay_spikes",
            }
        }
        assert anomaly_values["operations.downtime_spikes"] == 18.0
        assert anomaly_values["transport.fuel_use_rate_spikes"] == 11.8
        assert anomaly_values["transport.supplier_delay_spikes"] == 18.0

        again = await seed_demo_data(
            db,
            tenant_id=two_tenants["tenant_a"],
            admin_user_id=two_tenants["user_a"],
        )
        await db.commit()
        assert again["sources"] == 5
        assert again["rows"] == 108

        source_count = (
            await db.execute(
                select(DataSource.id).where(
                    DataSource.tenant_id == two_tenants["tenant_a"]
                )
            )
        ).scalars().all()
        staged_count = (
            await db.execute(
                select(StagedRow.id).where(
                    StagedRow.tenant_id == two_tenants["tenant_a"]
                )
            )
        ).scalars().all()
        forecast_count = (
            await db.execute(
                select(Forecast.id).where(
                    Forecast.tenant_id == two_tenants["tenant_a"],
                    Forecast.value_concept == "Finance.Cost",
                    Forecast.status == "ok",
                )
            )
        ).scalars().all()
        assert len(source_count) == 5
        assert len(staged_count) == 108
        assert len(forecast_count) == 1

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
    assert "Semantic understanding" in overview.text
    assert "One business meaning, many source formats" in overview.text
    assert "Buy Price" in overview.text
    assert "Purchase Price" in overview.text
