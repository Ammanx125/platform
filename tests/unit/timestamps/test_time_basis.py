# tests/unit/timestamps/test_time_basis.py
import pytest

from app.services.timestamps.constants import ALL_BASES, TimeBasis
from app.services.timestamps.service import (
    _parse_content_timestamp,
    _validate_kind,
)


def test_all_bases_contains_four():
    assert ALL_BASES == {"content", "ingestion", "file", "stream"}


def test_time_basis_compares_to_string():
    assert TimeBasis.INGESTION == "ingestion"
    assert TimeBasis.CONTENT.value == "content"


def test_validate_kind_accepts_known():
    for b in ALL_BASES:
        _validate_kind(b)


def test_validate_kind_rejects_unknown():
    with pytest.raises(ValueError, match="unknown timestamp kind"):
        _validate_kind("nonsense")


def test_parse_content_timestamp_assigns_utc_to_date_only_values():
    assert _parse_content_timestamp("2025-04-03").isoformat() == (
        "2025-04-03T00:00:00+00:00"
    )


def test_parse_content_timestamp_preserves_explicit_timezone():
    assert _parse_content_timestamp("2025-04-03T10:30:00+02:00").isoformat() == (
        "2025-04-03T10:30:00+02:00"
    )


def test_parse_content_timestamp_rejects_unparseable_values():
    with pytest.raises(ValueError):
        _parse_content_timestamp("not-a-date")