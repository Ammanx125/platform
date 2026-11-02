# tests/integration/test_decision_report_rendering.py
import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from app.db.models.action import ActionRecord
from app.db.models.decision import DecisionRun
from app.db.models.knowledge import Document
from app.db.session import SessionLocal
from app.schemas.report_structured import StructuredReport

EVIDENCE = {
    "kpis": [
        {
            "id": "kpi:procurement.total_spend",
            "key": "procurement.total_spend",
            "display_name": "Total Procurement Spend",
            "domain": "Procurement",
            "unit": "currency",
            "currency": "ETB",
            "description": "Total of all purchase prices.",
            "value": 1015.5,
            "rows_considered": 3,
        }
    ],
    "forecasts": [
        {
            "id": "forecast:Finance.Revenue:",
            "value_concept": "Finance.Revenue",
            "predicted_points": [
                {"timestamp": "2026-01-01", "value": 10},
                {"timestamp": "2026-02-01", "value": 20},
                {"timestamp": "2026-03-01", "value": 15},
            ],
        }
    ],
    "anomalies": [],
    "chunks": [
        {"id": "chunk:1", "content": "Ticket revenue rose 12% week over week."},
        {"id": "chunk:2", "content": "Route A carried 1,204 passengers."},
    ],
    "workflows": [],
    "email_summaries": [],
    "capability_errors": [{"capability": "customer_behavior", "error": "no customer data"}],
}


async def _login(client: AsyncClient, email: str, password: str) -> None:
    await client.post("/api/v1/auth/login", json={"email": email, "password": password})


async def _create_run(tenant_id, user_id, evidence) -> str:
    async with SessionLocal() as db:
        run = DecisionRun(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            user_id=user_id,
            query="How is revenue trending?",
            llm_summary="Revenue is rising.",
            evidence=evidence if evidence is not None else {},
            plan=[],
            validated_claims=[],
            dropped_claims=[],
            llm_tool_calls=[],
            source_ids=[],
            started_at=datetime.now(UTC),
        )
        db.add(run)
        await db.commit()
        return str(run.id)


async def _attach_structured_report(
    *,
    run_id: str,
    tenant_id,
    user_id,
) -> None:
    report = StructuredReport.model_validate(
        {
            "title": "Revenue management report",
            "sections": [
                {
                    "section_type": "summary",
                    "title": "Executive summary",
                    "insights": [
                        {
                            "observation": "Ticket revenue rose 12% week over week.",
                            "possible_causes": "Higher passenger volume.",
                            "business_impact": "Revenue is trending upward.",
                            "recommendation": "Monitor capacity on Route A.",
                            "evidence_ids": ["chunk:1"],
                        }
                    ],
                },
                {
                    "section_type": "evidence",
                    "title": "Evidence review",
                    "body": "The reported change is supported by retrieved records.",
                },
            ],
        }
    )
    async with SessionLocal() as db:
        document = Document(
            tenant_id=tenant_id,
            source_id=None,
            title=report.title,
            content_type="markdown",
            raw_text="# Revenue management report",
            doc_metadata={
                "generated_by": "generate_structured_report",
                "structured_report": report.model_dump(mode="json"),
            },
            status="ready",
        )
        db.add(document)
        await db.flush()
        db.add(
            ActionRecord(
                tenant_id=tenant_id,
                decision_run_id=uuid.UUID(run_id),
                user_id=user_id,
                tool_name="generate_structured_report",
                arguments={},
                status="executed",
                validation_result={},
                execution_result={"output": {"document_id": str(document.id)}},
                proposed_at=datetime.now(UTC),
            )
        )
        await db.commit()


@pytest.mark.asyncio
async def test_decision_detail_renders_the_report(client: AsyncClient, two_tenants: dict) -> None:
    await _login(client, two_tenants["email_a"], two_tenants["password"])
    run_id = await _create_run(two_tenants["tenant_a"], two_tenants["user_a"], EVIDENCE)

    response = await client.get(f"/decisions/{run_id}")

    assert response.status_code == 200
    html = response.text

    # KPI card
    assert 'class="kpi-cards"' in html
    assert "Total Procurement Spend" in html
    assert "1,015.50" in html
    assert "ETB" in html

    # Chart
    assert 'class="sparkline"' in html
    assert "<rect" in html
    # Three forecast points -> three bars
    assert html.count("<rect") >= 3

    # Table of the retrieved rows
    assert "Supporting evidence" in html
    assert "chunk:1" in html

    # The report replaced the raw evidence dump: nothing is rendered twice.
    assert html.count("Total Procurement Spend") == 1
    assert html.count("chunk:1") == 1
    # The query appears in the page <h1> and once as the report title, but
    # no longer in a separate "Question" block.
    assert html.count("How is revenue trending?") == 2
    assert '<div class="brief-section-label">Question</div>' not in html

    # Limitations, including the failed capability
    assert "What this does not include" in html
    assert "customer_behavior did not run: no customer data" in html


@pytest.mark.asyncio
async def test_decision_detail_renders_stored_structured_report(
    client: AsyncClient, two_tenants: dict
) -> None:
    await _login(client, two_tenants["email_a"], two_tenants["password"])
    run_id = await _create_run(two_tenants["tenant_a"], two_tenants["user_a"], EVIDENCE)
    await _attach_structured_report(
        run_id=run_id,
        tenant_id=two_tenants["tenant_a"],
        user_id=two_tenants["user_a"],
    )

    response = await client.get(f"/decisions/{run_id}")

    assert response.status_code == 200
    html = response.text
    assert "Revenue management report" in html
    assert "Executive summary" in html
    assert "Ticket revenue rose 12% week over week." in html
    assert "Possible causes: Higher passenger volume." in html
    assert "Impact: Revenue is trending upward." in html
    assert "Recommendation: Monitor capacity on Route A." in html
    assert "Evidence: chunk:1" in html
    assert "What this does not include" in html
    assert "customer_behavior did not run: no customer data" in html
    # KPI cards remain available, while the structured body replaces the
    # fallback chart and raw evidence table.
    assert "Total Procurement Spend" in html
    assert 'class="sparkline"' not in html
    assert "Supporting evidence" not in html


@pytest.mark.asyncio
async def test_decision_detail_without_evidence_still_renders(
    client: AsyncClient, two_tenants: dict
) -> None:
    await _login(client, two_tenants["email_a"], two_tenants["password"])
    run_id = await _create_run(two_tenants["tenant_a"], two_tenants["user_a"], None)

    response = await client.get(f"/decisions/{run_id}")

    assert response.status_code == 200
    # No cards, chart or table to show...
    assert 'class="kpi-cards"' not in response.text
    assert 'class="sparkline"' not in response.text
    # ...but the report still says so, rather than rendering an empty shell.
    assert "No evidence was recorded for this decision." in response.text
    assert "What this does not include" in response.text


@pytest.mark.asyncio
async def test_decision_report_is_tenant_scoped(client: AsyncClient, two_tenants: dict) -> None:
    run_id = await _create_run(two_tenants["tenant_b"], two_tenants["user_b"], EVIDENCE)
    await _login(client, two_tenants["email_a"], two_tenants["password"])

    assert (await client.get(f"/decisions/{run_id}")).status_code == 404


@pytest.mark.asyncio
async def test_flat_forecast_series_renders_without_dividing_by_zero(
    client: AsyncClient, two_tenants: dict
) -> None:
    """min == max must not produce a NaN coordinate or a crash."""
    evidence = {
        "kpis": [],
        "forecasts": [
            {
                "id": "forecast:flat",
                "value_concept": "Finance.Revenue",
                "predicted_points": [
                    {"timestamp": "2026-01-01", "value": 500},
                    {"timestamp": "2026-02-01", "value": 500},
                ],
            }
        ],
        "anomalies": [],
        "chunks": [],
    }
    await _login(client, two_tenants["email_a"], two_tenants["password"])
    run_id = await _create_run(two_tenants["tenant_a"], two_tenants["user_a"], evidence)

    response = await client.get(f"/decisions/{run_id}")

    assert response.status_code == 200
    assert 'class="sparkline"' in response.text
    # A zero spread must not produce NaN coordinates in the SVG. Check the
    # bars themselves (not the viewBox): every attribute must be a number.
    bars = response.text.split("<rect")[1:]
    assert len(bars) == 2
    for bar in bars:
        for attribute in ("x=", "y=", "width=", "height="):
            value = bar.split(attribute)[1].split('"')[1]
            number = float(value)  # raises on '', 'nan' and 'inf'
            assert number == number and abs(number) != float("inf")
    # Both flat points are drawn (at equal height), not dropped.
    heights = [bar.split('height="')[1].split('"')[0] for bar in bars]
    assert heights[0] == heights[1]
