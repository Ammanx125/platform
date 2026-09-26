# tests/unit/analytics/test_frequency.py
import math
from datetime import UTC, datetime, timedelta

from app.services.analytics.frequency import detect_frequency
from app.services.analytics.series import TimeSeries, TimeSeriesPoint


def _series(values: list[float], gap_days: int = 1) -> TimeSeries:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    return TimeSeries(
        group_key="",
        group_label="",
        value_concept="x",
        group_by_concept=None,
        points=[
            TimeSeriesPoint(
                timestamp=base + timedelta(days=i * gap_days),
                value=v,
                source_id=None,  # type: ignore[arg-type]
            )
            for i, v in enumerate(values)
        ],
    )


def test_daily_detection():
    s = _series([1.0] * 20, gap_days=1)
    f = detect_frequency(s)
    assert f.interval == "daily"


def test_weekly_detection():
    s = _series([1.0] * 20, gap_days=7)
    f = detect_frequency(s)
    assert f.interval == "weekly"


def test_seasonality_detected_on_periodic_signal():
    # Sine with period 7
    values = [math.sin(2 * math.pi * i / 7) for i in range(60)]
    s = _series(values, gap_days=1)
    f = detect_frequency(s)
    assert f.has_seasonality
    assert f.seasonality_period == 7


def test_no_seasonality_on_constant():
    s = _series([5.0] * 40, gap_days=1)
    f = detect_frequency(s)
    assert not f.has_seasonality