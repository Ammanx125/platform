"""Tenant, input-integrity, persistence, and freshness tests for Scenario Lab."""

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

SCENARIO_TYPE = "profit_sensitivity_v1"
DEFAULT_ASSUMPTIONS = {
    "supplier_cost_change_pct": "12",
    "selling_price_change_pct": "0",
    "volume_change_pct": "0",
}


async def _make_ready_source(
    *,
    tenant_id: uuid.UUID,
    name: str = "Profit and loss",
    rows: list[dict] | None = None,
    confirm_mappings: bool = True,
) -> uuid.UUID:
    rows = rows or [{"revenue": "1000000", "cogs": "700000", "currency": "USD"}]
    async with SessionLocal() as db:
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
            status="succeeded",
            rows_staged=len(rows),
        )
        db.add(job)
        await db.flush()
        for row_number, row in enumerate(rows, start=1):
            db.add(
                StagedRow(
                    tenant_id=tenant_id,
                    source_id=source.id,
                    job_id=job.id,
                    row_number=row_number,
                    raw_data=row,
                    search_text=" ".join(str(value) for value in row.values()),
                )
            )
        for concept, column in (
            ("Finance.Revenue", "revenue"),
            ("Finance.CostOfGoodsSold", "cogs"),
            ("Finance.Currency", "currency"),
        ):
            db.add(
                SemanticMapping(
                    tenant_id=tenant_id,
                    source_id=source.id,
                    canonical_concept_key=concept,
                    source_column=column,
                    status="confirmed" if confirm_mappings else "proposed",
                    confidence=1.0,
                    rationale={"method": "manual"},
                )
            )
        await db.commit()
        return source.id


async def _auth(client: AsyncClient, email: str, password: str) -> dict[str, str]:
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    token = client.cookies.get("sansa_csrf")
    return {"X-CSRF-Token": token} if token else {}


def _request(source_id: uuid.UUID, assumptions: dict | None = None) -> dict:
    return {
        "scenario_type": SCENARIO_TYPE,
        "source_id": str(source_id),
        "assumptions": assumptions or DEFAULT_ASSUMPTIONS,
    }


@pytest.mark.asyncio
async def test_preview_matches_plan_calculation_and_returns_evidence(client, two_tenants):
    source_id = await _make_ready_source(tenant_id=two_tenants["tenant_a"])
    headers = await _auth(client, two_tenants["email_a"], two_tenants["password"])

    response = await client.post(
        "/api/v1/scenarios/preview", headers=headers, json=_request(source_id)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "calculated"
    assert body["result"]["scenario_type"] == SCENARIO_TYPE
    assert body["result"]["currency"] == "USD"
    assert Decimal(body["result"]["baseline"]["revenue"]) == Decimal("1000000.00")
    assert Decimal(body["result"]["baseline"]["cogs"]) == Decimal("700000.00")
    assert Decimal(body["result"]["baseline"]["gross_profit"]) == Decimal("300000.00")
    assert Decimal(body["result"]["baseline"]["gross_margin_pct"]) == Decimal("30.00")
    assert Decimal(body["result"]["scenario"]["revenue"]) == Decimal("1000000.00")
    assert Decimal(body["result"]["scenario"]["cogs"]) == Decimal("784000.00")
    assert Decimal(body["result"]["scenario"]["gross_profit"]) == Decimal("216000.00")
    assert Decimal(body["result"]["delta"]["gross_profit"]) == Decimal("-84000.00")
    assert Decimal(body["result"]["delta"]["gross_profit_pct"]) == Decimal("-28.00")
    assert Decimal(body["result"]["delta"]["gross_margin_percentage_points"]) == Decimal("-8.40")
    assert body["provenance"]["cogs_concept"] == "Finance.CostOfGoodsSold"
    assert body["provenance"]["currency"] == "USD"
    assert body["result"]["limitations"]


@pytest.mark.asyncio
async def test_preview_applies_independent_price_and_volume_assumptions(client, two_tenants):
    source_id = await _make_ready_source(tenant_id=two_tenants["tenant_a"])
    headers = await _auth(client, two_tenants["email_a"], two_tenants["password"])
    assumptions = {
        "supplier_cost_change_pct": "12",
        "selling_price_change_pct": "10",
        "volume_change_pct": "-10",
    }

    response = await client.post(
        "/api/v1/scenarios/preview",
        headers=headers,
        json=_request(source_id, assumptions),
    )

    assert response.status_code == 200, response.text
    scenario = response.json()["result"]["scenario"]
    assert Decimal(scenario["revenue"]) == Decimal("990000.00")
    assert Decimal(scenario["cogs"]) == Decimal("705600.00")
    assert Decimal(scenario["gross_profit"]) == Decimal("284400.00")


@pytest.mark.asyncio
async def test_scenario_lab_is_reachable_from_workspace_navigation(client, two_tenants):
    await _make_ready_source(tenant_id=two_tenants["tenant_a"])
    await _auth(client, two_tenants["email_a"], two_tenants["password"])

    response = await client.get("/scenarios")

    assert response.status_code == 200
    assert 'class="shell shell--scenario-lab"' in response.text
    assert 'href="/scenarios" aria-current="page">Scenario Lab</a>' in response.text


@pytest.mark.asyncio
async def test_scenario_lab_renders_without_a_ready_source(client, two_tenants):
    await _auth(client, two_tenants["email_a"], two_tenants["password"])

    response = await client.get("/scenarios")

    assert response.status_code == 200
    assert "There is no active data source yet." in response.text


@pytest.mark.asyncio
async def test_html_preview_displays_profit_metrics_and_save_assumptions(client, two_tenants):
    source_id = await _make_ready_source(tenant_id=two_tenants["tenant_a"])
    await _auth(client, two_tenants["email_a"], two_tenants["password"])

    response = await client.post(
        "/scenarios/preview",
        data={
            "source_id": str(source_id),
            "supplier_cost_change_pct": "12",
            "selling_price_change_pct": "0",
            "volume_change_pct": "0",
        },
    )

    assert response.status_code == 200
    assert "Gross profit" in response.text
    assert "216,000.00" in response.text
    assert "Supplier cost" in response.text
    assert "12%" in response.text


@pytest.mark.asyncio
async def test_html_duplicate_prefills_saved_assumptions(client, two_tenants):
    source_id = await _make_ready_source(tenant_id=two_tenants["tenant_a"])
    headers = await _auth(client, two_tenants["email_a"], two_tenants["password"])
    created = await client.post(
        "/api/v1/scenarios",
        headers=headers,
        json={
            **_request(
                source_id,
                {
                    "supplier_cost_change_pct": "15",
                    "selling_price_change_pct": "2",
                    "volume_change_pct": "-5",
                },
            ),
            "name": "Duplicate me",
        },
    )
    assert created.status_code == 201, created.text

    response = await client.get("/scenarios", params={"from": created.json()["id"]})

    assert response.status_code == 200
    assert 'name="supplier_cost_change_pct"' in response.text
    assert 'value="15"' in response.text
    assert 'value="2"' in response.text
    assert 'value="-5"' in response.text


@pytest.mark.asyncio
async def test_html_save_redirects_with_confirmation_flag(client, two_tenants):
    source_id = await _make_ready_source(tenant_id=two_tenants["tenant_a"])
    await _auth(client, two_tenants["email_a"], two_tenants["password"])

    response = await client.post(
        "/scenarios/save",
        data={
            "csrf_token": client.cookies.get("sansa_csrf"),
            "name": "Cost +12%",
            "source_id": str(source_id),
            **DEFAULT_ASSUMPTIONS,
        },
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/scenarios?saved=1"
    saved_page = await client.get(response.headers["location"])
    assert 'id="lab-toast" role="status" data-show="true"' in saved_page.text


@pytest.mark.asyncio
async def test_legacy_saved_scenarios_are_visible_but_not_misreported(client, two_tenants):
    tenant = two_tenants["tenant_a"]
    source_id = await _make_ready_source(tenant_id=tenant)
    headers = await _auth(client, two_tenants["email_a"], two_tenants["password"])
    async with SessionLocal() as db:
        legacy = WhatIfScenario(
            tenant_id=tenant,
            name="Old spend scenario",
            scenario_type="supplier_spend_sensitivity_v1",
            source_id=source_id,
            status="calculated",
            assumptions={"supplier_price_change_pct": "5"},
            baseline_snapshot={},
            result={
                "baseline": {"projected_spend": "100"},
                "scenario": {"projected_spend": "105"},
            },
            provenance={},
        )
        db.add(legacy)
        await db.commit()
        legacy_id = legacy.id

    detail = await client.get(f"/api/v1/scenarios/{legacy_id}", headers=headers)
    summaries = await client.get("/api/v1/scenarios", headers=headers)
    page = await client.get("/scenarios")

    assert detail.status_code == 200
    assert detail.json()["supported"] is False
    assert detail.json()["result"] is None
    assert detail.json()["compatibility_note"]
    legacy_summary = next(
        scenario for scenario in summaries.json() if scenario["id"] == str(legacy_id)
    )
    assert legacy_summary["supported"] is False
    assert legacy_summary["baseline_gross_profit"] is None
    assert "Unavailable (legacy format)" in page.text


@pytest.mark.asyncio
async def test_missing_cogs_mapping_is_named_and_blocks_calculation(client, two_tenants):
    source_id = await _make_ready_source(tenant_id=two_tenants["tenant_a"], confirm_mappings=False)
    headers = await _auth(client, two_tenants["email_a"], two_tenants["password"])

    response = await client.post(
        "/api/v1/scenarios/preview", headers=headers, json=_request(source_id)
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "missing_mappings"
    assert "Finance.CostOfGoodsSold" in detail["missing_concepts"]


@pytest.mark.asyncio
async def test_another_tenants_source_is_not_found(client, two_tenants):
    source_id = await _make_ready_source(tenant_id=two_tenants["tenant_a"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client_b:
        headers = await _auth(client_b, two_tenants["email_b"], two_tenants["password"])
        response = await client_b.post(
            "/api/v1/scenarios/preview",
            headers=headers,
            json=_request(source_id),
        )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_saved_scenario_is_invisible_to_other_tenants(client, two_tenants):
    source_id = await _make_ready_source(tenant_id=two_tenants["tenant_a"])
    headers = await _auth(client, two_tenants["email_a"], two_tenants["password"])
    created = await client.post(
        "/api/v1/scenarios",
        headers=headers,
        json={**_request(source_id), "name": "Cost +12%"},
    )
    assert created.status_code == 201, created.text
    scenario_id = created.json()["id"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client_b:
        await _auth(client_b, two_tenants["email_b"], two_tenants["password"])
        assert (await client_b.get(f"/api/v1/scenarios/{scenario_id}")).status_code == 404
        other_list = await client_b.get("/api/v1/scenarios")
        assert scenario_id not in [row["id"] for row in other_list.json()]


@pytest.mark.asyncio
async def test_preview_and_save_do_not_modify_source_rows(client, two_tenants):
    source_id = await _make_ready_source(tenant_id=two_tenants["tenant_a"])
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
        json=_request(
            source_id,
            {
                "supplier_cost_change_pct": "30",
                "selling_price_change_pct": "0",
                "volume_change_pct": "0",
            },
        ),
    )
    saved = await client.post(
        "/api/v1/scenarios",
        headers=headers,
        json={
            **_request(
                source_id,
                {
                    "supplier_cost_change_pct": "30",
                    "selling_price_change_pct": "0",
                    "volume_change_pct": "0",
                },
            ),
            "name": "Read-only check",
        },
    )
    assert saved.status_code == 201, saved.text

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
async def test_out_of_range_assumptions_are_rejected(client, two_tenants):
    source_id = await _make_ready_source(tenant_id=two_tenants["tenant_a"])
    headers = await _auth(client, two_tenants["email_a"], two_tenants["password"])
    assumptions = {
        "supplier_cost_change_pct": "31",
        "selling_price_change_pct": "0",
        "volume_change_pct": "0",
    }

    response = await client.post(
        "/api/v1/scenarios/preview",
        headers=headers,
        json=_request(source_id, assumptions),
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_unknown_assumption_field_is_rejected(client, two_tenants):
    source_id = await _make_ready_source(tenant_id=two_tenants["tenant_a"])
    headers = await _auth(client, two_tenants["email_a"], two_tenants["password"])
    assumptions = {**DEFAULT_ASSUMPTIONS, "demand_elasticity": "1.2"}

    response = await client.post(
        "/api/v1/scenarios/preview",
        headers=headers,
        json=_request(source_id, assumptions),
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_mixed_currency_baseline_is_refused(client, two_tenants):
    source_id = await _make_ready_source(
        tenant_id=two_tenants["tenant_a"],
        rows=[
            {"revenue": "100", "cogs": "70", "currency": "USD"},
            {"revenue": "100", "cogs": "70", "currency": "EUR"},
        ],
    )
    headers = await _auth(client, two_tenants["email_a"], two_tenants["password"])

    response = await client.post(
        "/api/v1/scenarios/preview", headers=headers, json=_request(source_id)
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "mixed_currencies"


@pytest.mark.asyncio
async def test_incomplete_financial_rows_block_instead_of_being_dropped(client, two_tenants):
    source_id = await _make_ready_source(
        tenant_id=two_tenants["tenant_a"],
        rows=[
            {"revenue": "100", "cogs": "70", "currency": "USD"},
            {"revenue": "50", "currency": "USD"},
        ],
    )
    headers = await _auth(client, two_tenants["email_a"], two_tenants["password"])

    response = await client.post(
        "/api/v1/scenarios/preview", headers=headers, json=_request(source_id)
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "incomplete_data"


@pytest.mark.asyncio
async def test_compare_requires_same_source_and_currency(client, two_tenants):
    tenant = two_tenants["tenant_a"]
    source_id = await _make_ready_source(tenant_id=tenant)
    headers = await _auth(client, two_tenants["email_a"], two_tenants["password"])
    ids = []
    for price_change in ("5", "12"):
        payload = {
            "scenario_type": SCENARIO_TYPE,
            "source_id": str(source_id),
            "name": f"Cost +{price_change}%",
            "assumptions": {
                **DEFAULT_ASSUMPTIONS,
                "supplier_cost_change_pct": price_change,
            },
        }
        response = await client.post("/api/v1/scenarios", headers=headers, json=payload)
        assert response.status_code == 201, response.text
        ids.append(response.json()["id"])

    response = await client.post(
        "/api/v1/scenarios/compare",
        headers=headers,
        json={"scenario_ids": ids},
    )

    assert response.status_code == 200, response.text
    assert response.json()["source_id"] == str(source_id)
    assert response.json()["currency"] == "USD"
    assert len(response.json()["scenarios"]) == 2

    html_response = await client.get(
        "/scenarios/compare", params=[("ids", ids[0]), ("ids", ids[1])]
    )
    assert html_response.status_code == 200
    assert "Cost +5%" in html_response.text
    assert "Cost +12%" in html_response.text
    assert "Scenario gross profit" in html_response.text


@pytest.mark.asyncio
async def test_stale_scan_preserves_saved_result_then_recalculate_refreshes_it(client, two_tenants):
    tenant = two_tenants["tenant_a"]
    source_id = await _make_ready_source(tenant_id=tenant)
    headers = await _auth(client, two_tenants["email_a"], two_tenants["password"])
    created = await client.post(
        "/api/v1/scenarios",
        headers=headers,
        json={**_request(source_id), "name": "Before refresh"},
    )
    assert created.status_code == 201, created.text
    scenario_id = created.json()["id"]
    saved_profit = created.json()["result"]["scenario"]["gross_profit"]

    async with SessionLocal() as db:
        job = IngestionJob(
            tenant_id=tenant,
            source_id=source_id,
            status="succeeded",
            rows_staged=1,
        )
        db.add(job)
        await db.flush()
        db.add(
            StagedRow(
                tenant_id=tenant,
                source_id=source_id,
                job_id=job.id,
                row_number=1,
                raw_data={"revenue": "1000", "cogs": "700", "currency": "USD"},
                search_text="1000 700 USD",
            )
        )
        await db.commit()

    scan = await client.post("/api/v1/scenarios/stale-scan", headers=headers)
    assert scan.status_code == 200
    assert scan.json()["marked_stale"] >= 1
    stale = await client.get(f"/api/v1/scenarios/{scenario_id}", headers=headers)
    assert stale.json()["is_stale"] is True
    assert stale.json()["result"]["scenario"]["gross_profit"] == saved_profit

    recalculated = await client.post(
        f"/api/v1/scenarios/{scenario_id}/recalculate", headers=headers
    )
    assert recalculated.status_code == 200, recalculated.text
    assert recalculated.json()["is_stale"] is False
    assert Decimal(recalculated.json()["result"]["scenario"]["gross_profit"]) == Decimal(
        "216216.00"
    )


@pytest.mark.asyncio
async def test_stale_scan_detects_changed_confirmed_mapping(client, two_tenants):
    tenant = two_tenants["tenant_a"]
    source_id = await _make_ready_source(tenant_id=tenant)
    headers = await _auth(client, two_tenants["email_a"], two_tenants["password"])
    created = await client.post(
        "/api/v1/scenarios",
        headers=headers,
        json={**_request(source_id), "name": "Mapping baseline"},
    )
    assert created.status_code == 201, created.text

    async with SessionLocal() as db:
        revenue_mapping = (
            await db.execute(
                select(SemanticMapping).where(
                    SemanticMapping.tenant_id == tenant,
                    SemanticMapping.source_id == source_id,
                    SemanticMapping.canonical_concept_key == "Finance.Revenue",
                )
            )
        ).scalar_one()
        revenue_mapping.source_column = "net_sales"
        row = (
            await db.execute(select(StagedRow).where(StagedRow.source_id == source_id))
        ).scalar_one()
        row.raw_data = {**row.raw_data, "net_sales": row.raw_data["revenue"]}
        await db.commit()

    scan = await client.post("/api/v1/scenarios/stale-scan", headers=headers)
    assert scan.status_code == 200, scan.text
    assert scan.json()["marked_stale"] == 1


@pytest.mark.asyncio
async def test_capabilities_names_missing_financial_inputs(client, two_tenants):
    tenant = two_tenants["tenant_a"]
    ready = await _make_ready_source(tenant_id=tenant)
    headers = await _auth(client, two_tenants["email_a"], two_tenants["password"])
    async with SessionLocal() as db:
        bare = DataSource(
            tenant_id=tenant,
            name="No financial mappings",
            source_type="csv",
            is_active=True,
        )
        db.add(bare)
        await db.commit()
        bare_id = bare.id

    response = await client.get("/api/v1/scenarios/capabilities", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["scenario_type"] == SCENARIO_TYPE
    by_id = {source["source_id"]: source for source in body["sources"]}
    assert by_id[str(ready)]["supported"] is True
    assert by_id[str(bare_id)]["supported"] is False
    assert set(by_id[str(bare_id)]["missing_concepts"]) == {
        "Finance.Revenue",
        "Finance.CostOfGoodsSold",
        "Finance.Currency",
    }


@pytest.mark.asyncio
async def test_html_mutations_require_csrf_and_write_permission(client, two_tenants):
    headers = await _auth(client, two_tenants["email_a"], two_tenants["password"])
    no_csrf = await client.post("/scenarios/stale-scan", data={"csrf_token": "not-the-cookie"})
    assert no_csrf.status_code == 403

    async with SessionLocal() as db:
        roles = await seed_tenant_roles(db, tenant_id=two_tenants["tenant_a"])
        user = await db.get(User, two_tenants["user_a"])
        assert user is not None
        user.roles = [roles["viewer"]]
        await db.commit()

    denied = await client.post(
        "/scenarios/stale-scan",
        headers=headers,
        data={"csrf_token": client.cookies.get("sansa_csrf")},
    )
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_html_save_rejects_out_of_range_assumptions(client, two_tenants):
    source_id = await _make_ready_source(tenant_id=two_tenants["tenant_a"])
    await _auth(client, two_tenants["email_a"], two_tenants["password"])

    response = await client.post(
        "/scenarios/save",
        data={
            "csrf_token": client.cookies.get("sansa_csrf"),
            "name": "Out of range",
            "source_id": str(source_id),
            "supplier_cost_change_pct": "31",
            "selling_price_change_pct": "0",
            "volume_change_pct": "0",
        },
    )

    assert response.status_code == 422
