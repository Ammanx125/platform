# tests/unit/analytics/test_detectors.py
from datetime import UTC, datetime, timedelta

import pytest

from app.services.analytics.detectors import (
    DetectorError,
    detect_iqr,
    detect_isolation_forest,
    detect_zscore_global,
    detect_zscore_rolling,
)
from app.services.analytics.series import TimeSeries, TimeSeriesPoint


def _series(values: list[float]) -> TimeSeries:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    return TimeSeries(
        group_key="g",
        group_label="g",
        value_concept="x",
        group_by_concept=None,
        points=[
            TimeSeriesPoint(
                timestamp=base + timedelta(days=i),
                value=v,
                source_id=None,  # type: ignore[arg-type]
            )
            for i, v in enumerate(values)
        ],
    )


def test_zscore_global_flags_outlier():
    s = _series([10, 10, 10, 10, 10, 10, 10, 10, 10, 100])
    hits = detect_zscore_global(s, k=2.0)
    assert len(hits) == 1
    assert hits[0].value == 100.0
    assert hits[0].expected_max is not None


def test_zscore_global_returns_empty_for_constant():
    s = _series([5, 5, 5, 5, 5])
    assert detect_zscore_global(s, k=3.0) == []


def test_zscore_global_requires_min_points():
    s = _series([1, 2])
    assert detect_zscore_global(s, k=3.0) == []


def test_zscore_rolling_flags_spike_in_trend():
    # Steady trend with one spike
    values = [float(i) for i in range(20)]
    values[15] = 100.0
    s = _series(values)
    hits = detect_zscore_rolling(s, k=3.0, window=5)
    assert any(h.value == 100.0 for h in hits)


def test_zscore_rolling_requires_window():
    s = _series([1, 2, 3])
    assert detect_zscore_rolling(s, window=7) == []


def test_iqr_flags_extremes():
    s = _series([10, 11, 12, 13, 14, 15, 100])
    hits = detect_iqr(s, k=1.5)
    assert any(h.value == 100.0 for h in hits)


def test_iqr_requires_min_points():
    s = _series([1, 2, 3])
    assert detect_iqr(s) == []


def test_isolation_forest_raises_below_min():
    s = _series([float(i) for i in range(20)])
    with pytest.raises(DetectorError):
        detect_isolation_forest(s)


def test_isolation_forest_flags_outlier():
    values = [10.0] * 60 + [500.0]
    s = _series(values)
    hits = detect_isolation_forest(s, contamination=0.05)
    assert any(h.value == 500.0 for h in hits)