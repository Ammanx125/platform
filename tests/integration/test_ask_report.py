# tests/integration/test_ask_report.py
import pytest
from httpx import AsyncClient

EVIDENCE = {
    "kpis": [
        {
            "id": "kpi:procurement.total_spend",
            "key": "procurement.total_spend",
            "display_name": "Total Procurement Spend",
            "unit": "currency",
            "currency": "ETB",
            "description": "Total of all purchase prices.",
            "value": 1015.5,
        }
    ],
    "forecasts": [],
    "anomalies": [],
    "chunks": [{"id": "chunk:1", "content": "Acme supplied 100 bolts."}],
}


async def _login(client: AsyncClient, email: str, password: str) -> None:
    await client.post("/api/v1/auth/login", json={"email": email, "password": password})


@pytest.mark.asyncio
async def test_ask_endpoint_renders_a_structured_report(
    client: AsyncClient, two_tenants: dict
) -> None:
    await _login(client, two_tenants["email_a"], two_tenants["password"])
    token = client.cookies.get("sansa_csrf")
    headers = {"X-CSRF-Token": token} if token else {}

    response = await client.post(
        "/ask",
        data={"query": "How is procurement spend trending?"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert "Decision brief" in response.text

    # The route must have passed a report to the partial. Against the mock
    # LLM the planner may find no evidence, so the limitations block is what
    # proves the report reached the template (an empty report would render
    # nothing at all).
    assert "What this does not include" in response.text


@pytest.mark.asyncio
def test_answer_partial_renders_report_for_stored_evidence() -> None:
    """
    The _answer.html partial must render a report from evidence the same way
    the detail page does. Rendering the template directly keeps this
    independent of which capabilities the mock planner happens to choose.
    """
    from app.services.orchestration.report_builder import report_from_stored
    from app.web.templating import templates

    html = templates.get_template("decisions/_answer.html").render(
        error=None,
        decision_id="decisions/00000000-0000-0000-0000-000000000000",
        summary="Procurement spend totalled 1,015.50 ETB.",
        validated_claims=[],
        tool_calls=[],
        workflow=None,
        report=report_from_stored("q", EVIDENCE),
    )

    assert 'class="kpi-cards"' in html
    assert "Total Procurement Spend" in html
    assert "1,015.50" in html
    assert "ETB" in html
    assert "Supporting evidence" in html


def test_answer_partial_renders_structured_only_report() -> None:
    from app.schemas.report_structured import StructuredReport
    from app.services.orchestration.report_builder import report_from_stored
    from app.web.templating import templates

    report = report_from_stored(
        "q",
        None,
        structured_report=StructuredReport(
            title="Structured report",
            sections=[
                {
                    "section_type": "summary",
                    "title": "Summary",
                    "insights": [{"observation": "Revenue rose."}],
                }
            ],
        ),
    )
    html = templates.get_template("decisions/_answer.html").render(
        error=None,
        decision_id="00000000-0000-0000-0000-000000000000",
        summary="Revenue increased.",
        validated_claims=[],
        tool_calls=[],
        workflow=None,
        report=report,
    )

    assert "Structured report" in html
    assert "Revenue rose." in html
