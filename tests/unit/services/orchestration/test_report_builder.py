# tests/unit/services/orchestration/test_report_builder.py
import pytest

from app.services.orchestration.context import Evidence, EvidenceItem
from app.services.orchestration.report_builder import (
    _format_value,
    build_decision_report,
    report_from_stored,
)


def _kpi(
    value,
    *,
    key="procurement.total_spend",
    display="Total Procurement Spend",
    unit="currency",
    currency="ETB",
    text="Total Procurement Spend (ETB): 1,015.5",
    description=None,
):
    return EvidenceItem(
        kind="kpi",
        id=f"kpi:{key}",
        text=text,
        data={
            "id": f"kpi:{key}",
            "key": key,
            "display_name": display,
            "unit": unit,
            "currency": currency,
            "description": description,
            "value": value,
        },
    )


def _forecast(points, *, text="Forecast for Finance.Revenue"):
    return EvidenceItem(
        kind="forecast",
        id="forecast:Finance.Revenue:",
        text=text,
        data={
            "id": "forecast:Finance.Revenue:",
            "value_concept": "Finance.Revenue",
            "predicted_points": points,
        },
    )


def _chunk(n: int, content="a row of evidence"):
    return EvidenceItem(kind="chunk", id=f"chunk:{n}", text=content, data={"content": content})


# ---------- empty / limitations ----------


def test_empty_evidence_yields_title_and_one_limitation():
    report = build_decision_report(query="What is our revenue?", evidence=Evidence())

    assert report.title == "What is our revenue?"
    assert report.kpi_cards == []
    assert report.chart is None
    assert report.table is None
    assert len(report.limitations) == 1
    assert "No KPI or forecast evidence" in report.limitations[0]


def test_capability_errors_become_limitations():
    evidence = Evidence()
    evidence.kpis.append(_kpi(10.0))
    evidence.capability_errors.append(
        {"capability": "forecast", "error": "no forecast specification supplied"}
    )

    report = build_decision_report(query="q", evidence=evidence)

    assert any("forecast did not run" in line for line in report.limitations)
    assert any("no forecast specification" in line for line in report.limitations)


# ---------- KPI cards ----------


def test_one_kpi_yields_one_card_with_label_and_value():
    evidence = Evidence()
    evidence.kpis.append(_kpi(1_015.5))

    report = build_decision_report(query="q", evidence=evidence)

    assert len(report.kpi_cards) == 1
    card = report.kpi_cards[0]
    assert card.label == "Total Procurement Spend"
    assert card.value == "1,015.50"
    assert card.unit == "ETB"


def test_kpi_card_limit_is_six():
    evidence = Evidence()
    for i in range(9):
        evidence.kpis.append(_kpi(float(i), key=f"k{i}"))

    report = build_decision_report(query="q", evidence=evidence)

    assert len(report.kpi_cards) == 6


def test_card_prefers_description_over_evidence_text_for_context():
    evidence = Evidence()
    evidence.kpis.append(_kpi(5.0, description="Total of all purchase prices."))

    card = build_decision_report(query="q", evidence=evidence).kpi_cards[0]

    assert card.context == "Total of all purchase prices."


def test_currency_unit_is_not_repeated_on_the_card():
    """unit='currency' is a marker; the card shows 'ETB', not 'currency'."""
    evidence = Evidence()
    evidence.kpis.append(_kpi(5.0))

    card = build_decision_report(query="q", evidence=evidence).kpi_cards[0]

    assert card.unit == "ETB"


def test_non_currency_unit_is_shown_as_is():
    evidence = Evidence()
    evidence.kpis.append(_kpi(8.6, unit="litres_per_100km"))

    card = build_decision_report(query="q", evidence=evidence).kpi_cards[0]

    assert card.unit == "litres_per_100km"


# ---------- value formatting ----------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "—"),
        (1_000_000, "1,000,000"),
        (1015.5, "1,015.50"),
        (0, "0"),
        (float("nan"), "—"),
        (float("inf"), "—"),
        (True, "yes"),
        (False, "no"),
        ("text", "text"),
    ],
)
def test_format_value(value, expected):
    assert _format_value(value) == expected


def test_grouped_kpi_reports_group_count_not_a_total():
    evidence = Evidence()
    evidence.kpis.append(_kpi({"Acme": 100.0, "Beacon": 50.0}))

    card = build_decision_report(query="q", evidence=evidence).kpi_cards[0]

    assert card.value == "2 groups"


# ---------- chart ----------


def test_forecast_yields_chart_with_matching_lengths():
    points = [
        {"timestamp": "2026-01-01", "value": 10},
        {"timestamp": "2026-02-01", "value": 20},
        {"timestamp": "2026-03-01", "value": 15},
    ]
    evidence = Evidence()
    evidence.forecasts.append(_forecast(points))

    report = build_decision_report(query="q", evidence=evidence)

    assert report.chart is not None
    assert report.chart.kind == "line"
    assert report.chart.labels == ["2026-01-01", "2026-02-01", "2026-03-01"]
    assert report.chart.values == [10.0, 20.0, 15.0]
    assert len(report.chart.labels) == len(report.chart.values)
    # A forecast alone is not a limitation.
    assert not any("No KPI or forecast" in line for line in report.limitations)


def test_forecast_with_no_plottable_points_is_a_limitation():
    evidence = Evidence()
    evidence.forecasts.append(_forecast([{"timestamp": "2026-01-01", "value": None}]))

    report = build_decision_report(query="q", evidence=evidence)

    assert report.chart is None
    assert any("no plottable points" in line for line in report.limitations)


def test_forecast_points_with_bad_values_are_skipped_not_zeroed():
    points = [
        {"timestamp": "2026-01-01", "value": 10},
        {"timestamp": "2026-02-01", "value": "not a number"},
        {"timestamp": "2026-03-01"},
        {"timestamp": "2026-04-01", "value": 20},
    ]
    evidence = Evidence()
    evidence.forecasts.append(_forecast(points))

    report = build_decision_report(query="q", evidence=evidence)

    # Bad points are dropped rather than plotted as 0, which would fake a dip.
    assert report.chart is not None
    assert report.chart.values == [10.0, 20.0]
    assert report.chart.labels == ["2026-01-01", "2026-04-01"]


# ---------- table ----------


def test_three_chunks_yield_three_rows():
    evidence = Evidence()
    evidence.chunks.extend(_chunk(i) for i in range(3))

    report = build_decision_report(query="q", evidence=evidence)

    assert report.table is not None
    assert report.table.columns == ["ID", "Excerpt"]
    assert len(report.table.rows) == 3


def test_chunk_rows_are_capped():
    evidence = Evidence()
    evidence.chunks.extend(_chunk(i) for i in range(25))

    report = build_decision_report(query="q", evidence=evidence)

    assert report.table is not None
    assert len(report.table.rows) == 10


def test_anomalies_are_preferred_over_chunks():
    evidence = Evidence()
    evidence.chunks.append(_chunk(0))
    evidence.anomalies.append(
        EvidenceItem(
            kind="anomaly",
            id="anomaly:1",
            text="detector fired",
            data={"detector_key": "cost_spike", "group_label": "Route A", "value": 18.0},
        )
    )

    report = build_decision_report(query="q", evidence=evidence)

    assert report.table is not None
    assert report.table.title == "Anomalies detected"
    assert report.table.rows == [["cost_spike", "Route A", "18"]]


# ---------- stored evidence ----------


def test_report_from_stored_rehydrates_kpis_and_forecasts():
    stored = {
        "kpis": [
            {
                "id": "kpi:procurement.total_spend",
                "key": "procurement.total_spend",
                "display_name": "Total Procurement Spend",
                "unit": "currency",
                "currency": "ETB",
                "value": 1015.5,
            }
        ],
        "forecasts": [
            {
                "id": "forecast:x",
                "value_concept": "Finance.Revenue",
                "predicted_points": [
                    {"timestamp": "2026-01-01", "value": 1},
                    {"timestamp": "2026-02-01", "value": 2},
                ],
            }
        ],
        "anomalies": [],
        "chunks": [
            {"id": "chunk:1", "content": "supporting row"},
        ],
    }

    report = report_from_stored("q", stored)

    assert report.kpi_cards[0].label == "Total Procurement Spend"
    assert report.kpi_cards[0].unit == "ETB"
    assert report.chart is not None and len(report.chart.values) == 2
    assert report.table is not None
    assert report.table.title == "Supporting evidence"


def test_report_from_stored_handles_no_stored_evidence_at_all():
    """None means the run never wrote evidence, not that it found none."""
    report = report_from_stored("q", None)

    assert report.kpi_cards == []
    assert report.chart is None
    assert report.table is None
    assert "No KPI or forecast evidence" in report.limitations[0]


def test_report_from_stored_marks_an_empty_evidence_record():
    report = report_from_stored("q", {})

    assert report.kpi_cards == []
    assert "No evidence was recorded for this decision." in report.limitations


# ---------- structured reports do not inherit evidence limitations ----------


def _structured_report():
    from app.schemas.report_structured import ReportSection, StructuredReport

    return StructuredReport(
        title="Weekly Revenue Review",
        sections=[
            ReportSection(section_type="summary", title="Summary", body="Revenue grew."),
            ReportSection(section_type="kpis", title="Headline figures"),
        ],
    )


def test_structured_report_does_not_claim_evidence_is_missing():
    """
    A standalone structured report has no evidence payload by design. Its
    own KPI section must not sit above a footer saying no KPIs were found.
    """
    report = report_from_stored("Weekly Revenue Review", {}, structured_report=_structured_report())

    assert report.limitations == []


def test_structured_report_keeps_real_capability_limitations():
    stored = {
        "capability_errors": [{"capability": "forecast", "error": "no series"}],
    }
    report = report_from_stored("q", stored, structured_report=_structured_report())

    assert any("forecast did not run" in line for line in report.limitations)
    # ...but still no false "no evidence" line.
    assert not any("No KPI or forecast evidence" in line for line in report.limitations)


def test_decision_report_without_structure_still_reports_missing_evidence():
    """The fallback path is unchanged: nothing recorded is worth saying."""
    report = report_from_stored("q", {})
    assert "No evidence was recorded for this decision." in report.limitations
