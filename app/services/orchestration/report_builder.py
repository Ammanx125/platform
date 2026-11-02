# app/services/orchestration/report_builder.py
"""
Turn already-computed evidence into a display-ready DecisionReport.

No LLM. No DB. No charts library. Pure transformation, so it is cheap to
call on every page load and trivial to test.

Design notes:
  - The report never invents a number. Every card, bar and row comes from an
    EvidenceItem the executor produced.
  - Anything the report could not show is stated in `limitations`, including
    capability errors. A report that silently drops a failed KPI reads as a
    complete answer when it is not.
  - Forecast points store `timestamp` and `value`; anything else is skipped
    rather than rendered as a zero-height bar.
"""

from __future__ import annotations

from typing import Any

from app.schemas.report_structured import StructuredReport
from app.schemas.reports import ChartBlock, DecisionReport, KpiCard, TableBlock
from app.services.orchestration.context import Evidence

_MAX_KPI_CARDS = 6
_MAX_TABLE_ROWS = 10
_EXCERPT_CHARS = 100


def _format_value(value: Any) -> str:
    """Format a KPI value for display. Never raises."""
    if value is None:
        return "—"

    if isinstance(value, dict):
        # A grouped KPI: the value is the group count, not a total, so say
        # so rather than printing a number that would be misread as one.
        count = len(value)
        return f"{count} group{'' if count == 1 else 's'}"

    if isinstance(value, bool):
        return "yes" if value else "no"

    if isinstance(value, (int, float)):
        if value != value or value in (float("inf"), float("-inf")):  # NaN/inf
            return "—"
        if float(value).is_integer():
            return f"{int(value):,}"
        return f"{value:,.2f}"

    return str(value)


def _display_unit(unit: str | None, currency: str | None) -> str | None:
    """Label for a card. Currency codes read better than the word 'currency'."""
    if unit in (None, "currency") and currency:
        return currency
    if unit in (None, "currency"):
        return currency
    return unit


def _kpi_card(item: Any) -> KpiCard:
    data = item.data or {}
    unit = data.get("unit")
    currency = data.get("currency")

    context = data.get("description")
    if not context and item.text:
        context = item.text[:_EXCERPT_CHARS]

    return KpiCard(
        label=data.get("display_name") or data.get("key") or item.id,
        value=_format_value(data.get("value")),
        unit=_display_unit(unit, currency),
        context=context,
    )


def _forecast_chart(item: Any) -> ChartBlock | None:
    data = item.data or {}
    points = data.get("predicted_points") or []

    labels: list[str] = []
    values: list[float] = []
    for point in points:
        if not isinstance(point, dict):
            continue
        raw_value = point.get("value")
        if raw_value is None:
            continue
        try:
            numeric = float(raw_value)
        except (TypeError, ValueError):
            continue
        if numeric != numeric or numeric in (float("inf"), float("-inf")):
            continue
        labels.append(str(point.get("timestamp", "")))
        values.append(numeric)

    if not values:
        return None

    return ChartBlock(
        kind="line",
        title=item.text or data.get("value_concept") or "Forecast",
        labels=labels,
        values=values,
        unit=data.get("unit"),
    )


def _evidence_table(evidence: Evidence) -> TableBlock | None:
    """
    Rows that backed the answer.

    Anomalies are preferred over retrieved chunks: a detector firing is a
    concrete business observation, while a chunk is prose the answer may not
    even have used.
    """
    if evidence.anomalies:
        rows = []
        for item in evidence.anomalies[:_MAX_TABLE_ROWS]:
            data = item.data or {}
            rows.append(
                [
                    str(data.get("detector_key") or data.get("id") or ""),
                    str(data.get("group_label") or "—"),
                    _format_value(data.get("value")),
                ]
            )
        return TableBlock(
            title="Anomalies detected",
            columns=["Detector", "Group", "Value"],
            rows=rows,
        )

    if evidence.chunks:
        rows = []
        for item in evidence.chunks[:_MAX_TABLE_ROWS]:
            data = item.data or {}
            content = data.get("content") or item.text or ""
            rows.append([item.id, str(content)[:_EXCERPT_CHARS]])
        return TableBlock(
            title="Supporting evidence",
            columns=["ID", "Excerpt"],
            rows=rows,
        )

    return None


def build_decision_report(
    *,
    query: str,
    evidence: Evidence,
    structured_report: StructuredReport | None = None,
) -> DecisionReport:
    """
    Assemble a display-ready report from already-computed evidence.

    No LLM. No DB. Pure transformation.
    """
    report = DecisionReport(
        title=structured_report.title if structured_report is not None else query,
        structured=structured_report,
    )

    report.kpi_cards = [_kpi_card(item) for item in evidence.kpis[:_MAX_KPI_CARDS]]

    if evidence.forecasts:
        report.chart = _forecast_chart(evidence.forecasts[0])

    report.table = _evidence_table(evidence)

    if not evidence.kpis and not evidence.forecasts:
        report.limitations.append("No KPI or forecast evidence was available for this question.")
    if evidence.forecasts and report.chart is None:
        report.limitations.append("A forecast was produced but it contained no plottable points.")

    for failure in evidence.capability_errors:
        if not isinstance(failure, dict):
            continue
        capability = failure.get("capability") or "A capability"
        reason = failure.get("error") or "failed"
        report.limitations.append(f"{capability} did not run: {reason}")

    return report


def report_from_stored(
    query: str,
    stored: dict[str, Any] | None,
    *,
    structured_report: StructuredReport | None = None,
) -> DecisionReport:
    """
    Build a report from a DecisionRun.evidence JSONB payload.

    Stored evidence is a list of `data` dicts per category (see
    Evidence.to_dict), not EvidenceItems. Rehydrating here keeps the route
    honest: the report only ever sees what was persisted.
    """
    from app.services.orchestration.context import EvidenceItem

    evidence = Evidence()
    if not isinstance(stored, dict) or not stored:
        report = build_decision_report(
            query=query,
            evidence=evidence,
            structured_report=structured_report,
        )
        report.limitations.append("No evidence was recorded for this decision.")
        return report

    for kind, attribute in (
        ("kpis", "kpis"),
        ("forecasts", "forecasts"),
        ("anomalies", "anomalies"),
        ("chunks", "chunks"),
    ):
        for raw in stored.get(kind) or []:
            if not isinstance(raw, dict):
                continue
            getattr(evidence, attribute).append(
                EvidenceItem(
                    kind=kind,
                    id=str(raw.get("id", "")),
                    text=str(raw.get("text", "")),
                    data=raw,
                )
            )

    for raw in stored.get("capability_errors") or []:
        if isinstance(raw, dict):
            evidence.capability_errors.append(raw)

    return build_decision_report(
        query=query,
        evidence=evidence,
        structured_report=structured_report,
    )
