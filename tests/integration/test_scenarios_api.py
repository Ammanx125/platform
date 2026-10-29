# tests/integration/test_scenarios_api.py
"""
Scenario Lab integration tests.

These pin the things that are expensive to get wrong later: tenant
isolation, honest failure on missing prerequisites, read-only behaviour,
and staleness that never overwrites a saved result.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.db.models.dataset import DataSource, IngestionJob, StagedRow
from app.db.models.scenario import WhatIfScenario
from app.db.models.semantic import SemanticMapping
from app.db.models.user import User
from app.db.seed import ensure_permission_catalog, seed_tenant_roles
from app.db.seed_concepts import seed_canonical_concepts
from app.db.session import SessionLocal
from app.main import app

PRICE = "api-v1-scenarios-test-price"


async def _make_ready_source(
    *,
    tenant_id: uuid.UUID,
    name: str = "Supplier ledger",
    rows: list[dict] | None = None,
    confirm_mappings: bool = True,
) -> uuid.UUID:
    """
    One source with confirmed supplier/quantity/unit_price mappings and a
    handful of staged rows. Mirrors what ingestion + mapping confirmation
    leave behind, without running the whole ingest pipeline.
    """
    rows = rows or [
        {"supplier": "Alpha Fuel", "quantity": "100", "unit_price": "1000"},
        {"supplier": "Alpha Fuel", "quantity": "50", "unit_price": "1000"},
        {"supplier": "Beta Depot", "quantity": "200", "unit_price": "500"},
    ]
    async with SessionLocal() as db:
        # Canonical concepts are global and referenced by FK; make sure the
        # catalog exists (idempotent) before inserting mappings.
        await ensure_permission_catalog(db)
        await seed_canonical_concepts(db)
        source = DataSource(
            tenant_id=tenant_id,
            name=name,
            source_type="csv",
            snapshot_mode="append",
            is_active=True,
        )
        db.add(source)
        await db.flush()

        job = IngestionJob(
            tenant_id=tenant_id,
            source_id=source.id,
            status="completed",
            rows_staged=len(rows),
        )
        db.add(job)
        await db.flush()

        _added = []
        for row in rows:
            _added.append(row)
            db.add(
                StagedRow(
                    tenant_id=tenant_id,
                    source_id=source.id,
                    job_id=job.id,
                    row_number=len(_added) + 1,
                    raw_data=row,
                    search_text=" ".join(str(v) for v in row.values()),
                )
            )

        mappings = [
            ("Procurement.Supplier", "supplier"),
            ("Common.Quantity", "quantity"),
            ("Procurement.PurchasePrice", "unit_price"),
        ]
        for concept, column in mappings:
            db.add(
                SemanticMapping(
                    tenant_id=tenant_id,
                    source_id=source.id,
                    canonical_concept_key=concept,
                    source_column=column,
                    status="confirmed" if confirm_mappings else "proposed",
                    confidence=0.9,
                    rationale={"method": "manual"},
                )
            )
        await db.commit()
        return source.id


async def _auth(client: AsyncClient, email: str, password: str) -> dict[str, str]:
    """
    Log in the same way the browser does: the session lands in cookies and
    state-changing calls need the CSRF header. Logging in as tenant B
    replaces the cookies, so tests that need both logins use two clients.
    """
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    token = client.cookies.get("sansa_csrf")
    return {"X-CSRF-Token": token} if token else {}


@pytest.mark.asyncio
async def test_preview_matches_hand_calculation(client, two_tenants):
    tenant = two_tenants["tenant_a"]
    source_id = await _make_ready_source(tenant_id=tenant)
    headers = await _auth(client, two_tenants["email_a"], two_tenants["password"])

    response = await client.post(
        "/api/v1/scenarios/preview",
        headers=headers,
        json={
            "scenario_type": "supplier_spend_sensitivity_v1",
            "source_id": str(source_id),
            "assumptions": {"supplier_price_change_pct": "12"},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # Alpha = 150,000, Beta = 100,000 -> 250,000 baseline; +12% -> 280,000.
    assert Decimal(body["result"]["baseline"]["projected_spend"]) == Decimal("250000.00")
    assert Decimal(body["result"]["scenario"]["projected_spend"]) == Decimal("280000.00")
    assert Decimal(body["result"]["scenario"]["increase"]) == Decimal("30000.00")
    assert body["result"]["suppliers"][0]["supplier_key"] == "Alpha Fuel"
    assert body["result"]["limitations"]


@pytest.mark.asyncio
async def test_unconfirmed_mapping_is_refused_with_the_concept_named(client, two_tenants):
    tenant = two_tenants["tenant_a"]
    source_id = await _make_ready_source(tenant_id=tenant, confirm_mappings=False)
    headers = await _auth(client, two_tenants["email_a"], two_tenants["password"])

    response = await client.post(
        "/api/v1/scenarios/preview",
        headers=headers,
        json={
            "scenario_type": "supplier_spend_sensitivity_v1",
            "source_id": str(source_id),
            "assumptions": {"supplier_price_change_pct": "5"},
        },
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "missing_mappings"
    assert "Procurement.Supplier" in detail["missing_concepts"]


@pytest.mark.asyncio
async def test_another_tenants_source_is_not_found(client, two_tenants):
    source_id = await _make_ready_source(tenant_id=two_tenants["tenant_a"])
    # A separate client: logging tenant B in on the same client would
    # replace tenant A's session cookie.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client_b:
        headers_b = await _auth(client_b, two_tenants["email_b"], two_tenants["password"])
        response = await client_b.post(
            "/api/v1/scenarios/preview",
            headers=headers_b,
            json={
                "scenario_type": "supplier_spend_sensitivity_v1",
                "source_id": str(source_id),
                "assumptions": {"supplier_price_change_pct": "5"},
            },
        )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_saved_scenario_is_invisible_to_other_tenants(client, two_tenants):
    tenant = two_tenants["tenant_a"]
    source_id = await _make_ready_source(tenant_id=tenant)
    headers_a = await _auth(client, two_tenants["email_a"], two_tenants["password"])

    created = await client.post(
        "/api/v1/scenarios",
        headers=headers_a,
        json={
            "scenario_type": "supplier_spend_sensitivity_v1",
            "source_id": str(source_id),
            "name": "Supplier +12%",
            "assumptions": {"supplier_price_change_pct": "12"},
        },
    )
    assert created.status_code == 201, created.text
    scenario_id = created.json()["id"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client_b:
        await _auth(client_b, two_tenants["email_b"], two_tenants["password"])

        other_read = await client_b.get(f"/api/v1/scenarios/{scenario_id}")
        assert other_read.status_code == 404

        other_list = await client_b.get("/api/v1/scenarios")
        assert other_list.status_code == 200
        assert scenario_id not in [s["id"] for s in other_list.json()]


@pytest.mark.asyncio
async def test_preview_and_save_do_not_modify_source_rows(client, two_tenants):
    tenant = two_tenants["tenant_a"]
    source_id = await _make_ready_source(tenant_id=tenant)
    headers = await _auth(client, two_tenants["email_a"], two_tenants["password"])

    async with SessionLocal() as db:
        before = (
            await db.execute(
                select(func.count(StagedRow.id)).where(StagedRow.source_id == source_id)
            )
        ).scalar_one()
        before_data = (
            (await db.execute(select(StagedRow.raw_data).where(StagedRow.source_id == source_id)))
            .scalars()
            .all()
        )

    await client.post(
        "/api/v1/scenarios/preview",
        headers=headers,
        json={
            "scenario_type": "supplier_spend_sensitivity_v1",
            "source_id": str(source_id),
            "assumptions": {"supplier_price_change_pct": "30"},
        },
    )
    await client.post(
        "/api/v1/scenarios",
        headers=headers,
        json={
            "scenario_type": "supplier_spend_sensitivity_v1",
            "source_id": str(source_id),
            "name": "Read-only check",
            "assumptions": {"supplier_price_change_pct": "30"},
        },
    )

    async with SessionLocal() as db:
        after = (
            await db.execute(
                select(func.count(StagedRow.id)).where(StagedRow.source_id == source_id)
            )
        ).scalar_one()
        after_data = (
            (await db.execute(select(StagedRow.raw_data).where(StagedRow.source_id == source_id)))
            .scalars()
            .all()
        )

    assert before == after
    assert before_data == after_data


@pytest.mark.asyncio
async def test_out_of_range_assumption_is_rejected_by_schema(client, two_tenants):
    source_id = await _make_ready_source(tenant_id=two_tenants["tenant_a"])
    headers = await _auth(client, two_tenants["email_a"], two_tenants["password"])

    response = await client.post(
        "/api/v1/scenarios/preview",
        headers=headers,
        json={
            "scenario_type": "supplier_spend_sensitivity_v1",
            "source_id": str(source_id),
            "assumptions": {"supplier_price_change_pct": "400"},
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_html_save_requires_csrf(client, two_tenants):
    await _auth(client, two_tenants["email_a"], two_tenants["password"])

    response = await client.post(
        "/scenarios/save",
        data={
            "name": "No CSRF",
            "source_id": str(uuid.uuid4()),
            "supplier_price_change_pct": "5",
        },
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_html_scenario_mutation_requires_write_permission(client, two_tenants):
    headers = await _auth(client, two_tenants["email_a"], two_tenants["password"])
    async with SessionLocal() as db:
        roles = await seed_tenant_roles(db, tenant_id=two_tenants["tenant_a"])
        user = await db.get(User, two_tenants["user_a"])
        assert user is not None
        user.roles = [roles["viewer"]]
        await db.commit()

    response = await client.post(
        "/scenarios/stale-scan",
        headers=headers,
        data={"csrf_token": client.cookies.get("sansa_csrf")},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_html_save_rejects_out_of_range_assumption(client, two_tenants):
    source_id = await _make_ready_source(tenant_id=two_tenants["tenant_a"])
    await _auth(client, two_tenants["email_a"], two_tenants["password"])

    response = await client.post(
        "/scenarios/save",
        data={
            "csrf_token": client.cookies.get("sansa_csrf"),
            "name": "Out of range",
            "source_id": str(source_id),
            "supplier_price_change_pct": "101",
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_unknown_assumption_field_is_rejected(client, two_tenants):
    source_id = await _make_ready_source(tenant_id=two_tenants["tenant_a"])
    headers = await _auth(client, two_tenants["email_a"], two_tenants["password"])

    response = await client.post(
        "/api/v1/scenarios/preview",
        headers=headers,
        json={
            "scenario_type": "supplier_spend_sensitivity_v1",
            "source_id": str(source_id),
            "assumptions": {
                "supplier_price_change_pct": "5",
                "demand_elasticity": "1.2",
            },
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_stale_scan_flags_changed_baseline_without_touching_numbers(client, two_tenants):
    tenant = two_tenants["tenant_a"]
    source_id = await _make_ready_source(tenant_id=tenant)
    headers = await _auth(client, two_tenants["email_a"], two_tenants["password"])

    created = await client.post(
        "/api/v1/scenarios",
        headers=headers,
        json={
            "scenario_type": "supplier_spend_sensitivity_v1",
            "source_id": str(source_id),
            "name": "Before refresh",
            "assumptions": {"supplier_price_change_pct": "12"},
        },
    )
    assert created.status_code == 201, created.text
    original = created.json()
    scenario_id = original["id"]
    saved_projected = original["result"]["scenario"]["projected_spend"]

    # A new ingestion job arrives (same source, append mode) with a new row.
    async with SessionLocal() as db:
        job = IngestionJob(tenant_id=tenant, source_id=source_id, status="completed", rows_staged=1)
        db.add(job)
        await db.flush()
        db.add(
            StagedRow(
                tenant_id=tenant,
                source_id=source_id,
                job_id=job.id,
                row_number=4,
                raw_data={"supplier": "Gamma Oil", "quantity": "10", "unit_price": "1000"},
                search_text="Gamma Oil 10 1000",
            )
        )
        await db.commit()

    scan = await client.post("/api/v1/scenarios/stale-scan", headers=headers)
    assert scan.status_code == 200
    assert scan.json()["marked_stale"] >= 1

    reread = await client.get(f"/api/v1/scenarios/{scenario_id}", headers=headers)
    assert reread.status_code == 200
    body = reread.json()
    assert body["is_stale"] is True
    assert body["stale_reason"]
    # The number the manager saw when saving is still the number shown.
    assert body["result"]["scenario"]["projected_spend"] == saved_projected

    async with SessionLocal() as db:
        stored = await db.get(WhatIfScenario, uuid.UUID(scenario_id))
        assert stored is not None
        assert stored.status == "stale"


@pytest.mark.asyncio
async def test_recalculate_refreshes_a_stale_scenario(client, two_tenants):
    tenant = two_tenants["tenant_a"]
    source_id = await _make_ready_source(tenant_id=tenant)
    headers = await _auth(client, two_tenants["email_a"], two_tenants["password"])

    created = await client.post(
        "/api/v1/scenarios",
        headers=headers,
        json={
            "scenario_type": "supplier_spend_sensitivity_v1",
            "source_id": str(source_id),
            "name": "Then recalc",
            "assumptions": {"supplier_price_change_pct": "10"},
        },
    )
    scenario_id = created.json()["id"]

    async with SessionLocal() as db:
        job = IngestionJob(tenant_id=tenant, source_id=source_id, status="completed", rows_staged=1)
        db.add(job)
        await db.flush()
        db.add(
            StagedRow(
                tenant_id=tenant,
                source_id=source_id,
                job_id=job.id,
                row_number=4,
                raw_data={"supplier": "Gamma Oil", "quantity": "10", "unit_price": "1000"},
                search_text="Gamma Oil 10 1000",
            )
        )
        await db.commit()

    await client.post("/api/v1/scenarios/stale-scan", headers=headers)

    recalculated = await client.post(
        f"/api/v1/scenarios/{scenario_id}/recalculate", headers=headers
    )
    assert recalculated.status_code == 200, recalculated.text
    body = recalculated.json()
    assert body["is_stale"] is False
    assert body["stale_reason"] is None
    # Baseline grew by the new 10,000 line, then +10%.
    assert Decimal(body["result"]["baseline"]["projected_spend"]) == Decimal("260000.00")
    assert Decimal(body["result"]["scenario"]["projected_spend"]) == Decimal("286000.00")


@pytest.mark.asyncio
async def test_capabilities_explains_unsupported_sources(client, two_tenants):
    tenant = two_tenants["tenant_a"]
    ready = await _make_ready_source(tenant_id=tenant)
    headers = await _auth(client, two_tenants["email_a"], two_tenants["password"])

    async with SessionLocal() as db:
        bare = DataSource(tenant_id=tenant, name="No mappings", source_type="csv", is_active=True)
        db.add(bare)
        await db.commit()
        bare_id = bare.id

    response = await client.get("/api/v1/scenarios/capabilities", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["supported"] is True
    by_id = {s["source_id"]: s for s in body["sources"]}
    assert by_id[str(ready)]["supported"] is True
    assert by_id[str(bare_id)]["supported"] is False
    assert by_id[str(bare_id)]["code"] in {"missing_mappings", "no_staged_rows"}
