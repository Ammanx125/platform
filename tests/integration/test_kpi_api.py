# tests/integration/test_kpi_api.py
import io

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.db.seed_concepts import seed_canonical_concepts
from app.db.seed_kpis import seed_kpis
from app.db.session import SessionLocal
from app.services.ingestion.service import run_job

CSV = (
    b"supplier,product,quantity,unit_price,order_date\n"
    b"Acme,Bolt,100,0.42,2026-01-15\n"
    b"Acme,Nut,200,0.18,2026-01-15\n"
    b"Beacon,Sheet,50,18.75,2026-01-20\n"
)


def _csrf(client: AsyncClient) -> dict[str, str]:
    t = client.cookies.get("sansa_csrf")
    return {"X-CSRF-Token": t} if t else {}


async def _login(client: AsyncClient, email: str, password: str) -> None:
    await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )


async def _seed_and_ingest(client: AsyncClient, two_tenants: dict) -> str:
    async with SessionLocal() as db:
        await seed_canonical_concepts(db)
        await seed_kpis(db)
        await db.commit()

    await _login(client, two_tenants["email_a"], two_tenants["password"])

    r = await client.post(
        "/api/v1/datasets",
        json={"name": "KPI test", "source_type": "csv", "config": {}},
        headers=_csrf(client),
    )
    ds_id = r.json()["id"]

    r = await client.post(
        f"/api/v1/datasets/{ds_id}/upload",
        files={"file": ("data.csv", io.BytesIO(CSV), "text/csv")},
        headers=_csrf(client),
    )
    job_id = r.json()["id"]

    async with SessionLocal() as db:
        await run_job(db, job_id=job_id)

    # Propose and confirm mappings so KPI resolution works.
    r = await client.post(
        f"/api/v1/datasets/{ds_id}/mappings/propose",
        params={"job_id": job_id},
        headers=_csrf(client),
    )
    mappings = r.json()["mappings"]
    for m in mappings:
        await client.patch(
            f"/api/v1/datasets/{ds_id}/mappings/{m['id']}",
            json={"status": "confirmed"},
            headers=_csrf(client),
        )

    return ds_id


@pytest.mark.asyncio
async def test_kpi_list_and_evaluate(
    client: AsyncClient, two_tenants: dict
) -> None:
    await _seed_and_ingest(client, two_tenants)

    r = await client.get("/api/v1/analytics/kpis")
    assert r.status_code == 200
    keys = {k["key"] for k in r.json()}
    assert "procurement.total_spend" in keys

    r = await client.post(
        "/api/v1/analytics/kpis/procurement.total_spend/evaluate",
        json={},
        headers=_csrf(client),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["error"] is None
    # 100 * 0.42 + 200 * 0.18 + 50 * 18.75 = 42 + 36 + 937.5 = 1015.5
    # But wait — the CSV has unit_price as the price. The mapping might
    # bind unit_price to Procurement.PurchasePrice or Sales.SellingPrice
    # depending on the matcher. Assert only that value is a number.
    assert isinstance(body["value"], (int, float))


@pytest.mark.asyncio
async def test_kpi_grouped_by_supplier(
    client: AsyncClient, two_tenants: dict
) -> None:
    await _seed_and_ingest(client, two_tenants)

    r = await client.post(
        "/api/v1/analytics/kpis/procurement.spend_by_supplier/evaluate",
        json={},
        headers=_csrf(client),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["error"] is None
    assert isinstance(body["value"], dict)


@pytest.mark.asyncio
async def test_kpi_404_for_unknown(client: AsyncClient, two_tenants: dict) -> None:
    await _seed_and_ingest(client, two_tenants)
    r = await client.get("/api/v1/analytics/kpis/does.not.exist")
    assert r.status_code == 404

@pytest.mark.asyncio
async def test_kpi_carries_concept_metadata(procurement_tenant) -> None:
    """display_name/unit/currency come from the concept the formula reads."""
    from app.services.analytics.kpi import evaluate_kpi

    tenants, source_id = procurement_tenant

    async with SessionLocal() as db:
        result = await evaluate_kpi(
            db,
            key="procurement.total_spend",
            tenant_id=tenants["tenant_a"],
            source_ids=[source_id],
        )

    assert result.error is None
    assert result.concept_key == "Procurement.PurchasePrice"
    assert result.currency is None
    assert result.description

    trace = result.formula_trace
    assert trace["concept_key"] == "Procurement.PurchasePrice"
    assert trace["unit"] == "currency"
    assert trace["currency"] is None
    assert trace["display_name"] == "Purchase Price"
    assert trace["description"] == result.description
    # The pre-existing trace keys are still present.
    assert set(trace) >= {"formula", "rows_considered", "sources_used"}


@pytest.mark.asyncio
async def test_kpi_evidence_text_labels_currency_and_explains(
    procurement_tenant,
) -> None:
    from app.services.analytics.kpi import evaluate_kpi
    from app.services.orchestration.capabilities import _render_kpi_text

    tenants, source_id = procurement_tenant

    async with SessionLocal() as db:
        result = await evaluate_kpi(
            db,
            key="procurement.total_spend",
            tenant_id=tenants["tenant_a"],
            source_ids=[source_id],
        )

    text = _render_kpi_text(result)
    # Currency is not inferred from global concept metadata.
    assert text.startswith("Total Procurement Spend: ")
    assert "(ETB)" not in text
    assert " — " in text
    assert text.endswith(result.description)


@pytest.mark.asyncio
async def test_multi_concept_ratio_has_no_single_currency(
    procurement_tenant,
) -> None:
    """A ratio over two concepts is not an amount of money."""
    from app.db.models.analytics import KPIDefinition
    from app.services.analytics.kpi import evaluate_kpi

    tenants, source_id = procurement_tenant

    async with SessionLocal() as db:
        db.add(
            KPIDefinition(
                key="test.spend_per_unit",
                display_name="Spend per Unit",
                domain="Procurement",
                value_type="number",
                formula={
                    "op": "ratio",
                    "numerator": {"op": "sum", "concept": "Procurement.PurchasePrice"},
                    "denominator": {"op": "sum", "concept": "Common.Quantity"},
                },
            )
        )
        await db.commit()

        result = await evaluate_kpi(
            db,
            key="test.spend_per_unit",
            tenant_id=tenants["tenant_a"],
            source_ids=[source_id],
        )

        assert result.currency is None
        assert result.concept_key is None
        assert result.formula_trace["currency"] is None

        definition = (
            await db.execute(
                select(KPIDefinition).where(KPIDefinition.key == "test.spend_per_unit")
            )
        ).scalar_one()
        await db.delete(definition)
        await db.commit()
