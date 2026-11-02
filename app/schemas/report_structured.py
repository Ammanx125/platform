from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class Insight(BaseModel):
    """One evidence-backed observation in a report section."""

    observation: str = Field(min_length=1, max_length=2000)
    possible_causes: str | None = Field(default=None, max_length=1000)
    business_impact: str | None = Field(default=None, max_length=1000)
    recommendation: str | None = Field(default=None, max_length=1000)
    evidence_ids: list[str] = Field(default_factory=list)


class ReportSection(BaseModel):
    """A renderer-supported section of a structured report."""

    section_type: Literal[
        "summary",
        "kpis",
        "evidence",
        "attribution",
        "recommendations",
        "limitations",
    ]
    title: str = Field(min_length=1, max_length=200)
    body: str | None = Field(default=None, max_length=4000)
    insights: list[Insight] = Field(default_factory=list)


class StructuredReport(BaseModel):
    """Validated, durable structure for a generated management report."""

    title: str = Field(min_length=1, max_length=500)
    sections: list[ReportSection] = Field(min_length=1)

    @field_validator("sections")
    @classmethod
    def _at_least_one_foundational_section(
        cls, sections: list[ReportSection]
    ) -> list[ReportSection]:
        foundational = {"summary", "kpis", "evidence"}
        if not any(section.section_type in foundational for section in sections):
            raise ValueError(
                "a report must include at least one foundational section "
                "(summary, kpis, or evidence); it cannot consist solely of "
                "attribution, recommendations, or limitations"
            )
        return sections

    @field_validator("sections")
    @classmethod
    def _summary_first_if_present(cls, sections: list[ReportSection]) -> list[ReportSection]:
        if any(section.section_type == "summary" for section in sections[1:]):
            raise ValueError("if a summary section exists, it must be first")
        return sections
