# tests/unit/services/browser/test_job_params_schema.py
from __future__ import annotations

import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from app.schemas.browser import (
    BrowserExtractionJobParams,
    BrowserExtractionPaginationSpec,
    BrowserExtractionReportSpec,
)


def _make(**overrides):
    base: dict[str, Any] = dict(
        schema_version=1,
        run_id="abc",
        profile_id=uuid.uuid4(),
        profile_revision=1,
        source_id=uuid.uuid4(),
        authorization_id=uuid.uuid4(),
        approved_origin="https://erp.example.com",
        report_spec=BrowserExtractionReportSpec(
            start_url_path="/reports",
            report_nav=["Sales"],
        ),
        pagination_spec=BrowserExtractionPaginationSpec(),
        expected_columns=["date", "amount"],
        max_rows=100,
        max_pages=5,
        timeout_seconds=300,
    )
    base.update(overrides)
    return BrowserExtractionJobParams(**base)


def test_valid_params_pass():
    m = _make()
    assert m.output_format == "csv"
    assert m.schema_version == 1


def test_extra_field_rejected():
    with pytest.raises(ValidationError):
        _make(unexpected="nope")


def test_output_format_locked_to_csv():
    with pytest.raises(ValidationError):
        _make(output_format="json")


def test_report_spec_extra_field_rejected():
    with pytest.raises(ValidationError):
        BrowserExtractionReportSpec(
            start_url_path="/", script="alert(1)"  # type: ignore[call-arg]
        )


def test_pagination_method_restricted():
    with pytest.raises(ValidationError):
        BrowserExtractionPaginationSpec(method="arbitrary_python")  # type: ignore[arg-type]