# app/schemas/reports.py
"""
The display shape of a decision report.

This is a description of what a report looks like, not an engine. A
DecisionReport is assembled from evidence the orchestrator already
computed; nothing here fetches, computes, or asks a model for anything.

Values are pre-formatted strings so the template does no arithmetic and no
locale guessing — a manager screenshots the page and the numbers are right.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.report_structured import StructuredReport


class KpiCard(BaseModel):
    """One headline number."""

    label: str
    value: str  # pre-formatted, e.g. "1,000,000"
    unit: str | None = None
    context: str | None = None  # one-line description


class ChartBlock(BaseModel):
    """
    A single series rendered as inline SVG.

    kind is "none" when there is nothing to plot; the template skips it.
    """

    kind: Literal["line", "bar", "none"] = "none"
    title: str
    labels: list[str] = Field(default_factory=list)
    values: list[float] = Field(default_factory=list)
    unit: str | None = None


class TableBlock(BaseModel):
    title: str
    columns: list[str]
    rows: list[list[str]] = Field(default_factory=list)


class DecisionReport(BaseModel):
    title: str
    kpi_cards: list[KpiCard] = Field(default_factory=list)
    chart: ChartBlock | None = None
    table: TableBlock | None = None
    structured: StructuredReport | None = None
    limitations: list[str] = Field(default_factory=list)
