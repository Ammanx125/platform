import pytest
from pydantic import ValidationError

from app.schemas.report_structured import Insight, StructuredReport


def _section(section_type: str, **kwargs):
    return {"section_type": section_type, "title": section_type.title(), **kwargs}


def test_attribution_and_recommendations_alone_are_not_a_report():
    with pytest.raises(ValidationError, match="foundational section"):
        StructuredReport(
            title="Report",
            sections=[
                _section("attribution"),
                _section("recommendations"),
            ],
        )


def test_summary_must_be_first():
    with pytest.raises(ValidationError, match="must be first"):
        StructuredReport(
            title="Report",
            sections=[
                _section("kpis"),
                _section("summary"),
            ],
        )


def test_summary_first_is_valid():
    report = StructuredReport(
        title="Report",
        sections=[
            _section("summary"),
            _section("attribution"),
        ],
    )

    assert report.sections[0].section_type == "summary"


def test_kpis_without_summary_is_valid():
    report = StructuredReport(
        title="Report",
        sections=[_section("kpis")],
    )

    assert report.sections[0].section_type == "kpis"


def test_insight_observation_is_required_and_other_fields_are_optional():
    assert Insight(observation="Revenue increased").evidence_ids == []

    with pytest.raises(ValidationError):
        Insight()
