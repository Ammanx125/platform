# tests/unit/analytics/test_forecasting.py
from datetime import UTC, datetime, timedelta

import pytest

from app.services.analytics.forecasting import forecast_series
from app.services.analytics.series import TimeSeriesPoint


class _DBStub:
    """A no-op session stub — persist=False in these tests."""
    async def execute(self, *a, **k):
        raise AssertionError("db should not be touched")
    def add(self, *a, **k):
        raise AssertionError("db should not be touched")
    async def flush(self):
        raise AssertionError("db should not be touched")


def _points(values: list[float]) -> list[TimeSeriesPoint]:
    import uuid
    base = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        TimeSeriesPoint(
            timestamp=base + timedelta(days=i),
            value=v,
            source_id=uuid.uuid4(),
        )
        for i, v in enumerate(values)
    ]


@pytest.mark.asyncio
async def test_refuses_insufficient_history():
    result = await forecast_series(
        _DBStub(),  # type: ignore[arg-type]
        tenant_id=__import__("uuid").uuid4(),
        value_concept="X",
        group_key="",
        group_label="",
        points=_points([1.0, 2.0]),
        persist=False,
    )
    assert result.status == "insufficient_history"
    assert result.model_used is None
    assert result.predicted_points == []


@pytest.mark.asyncio
async def test_forecasts_a_clean_trend():
    values = [float(i) for i in range(40)]
    result = await forecast_series(
        _DBStub(),  # type: ignore[arg-type]
        tenant_id=__import__("uuid").uuid4(),
        value_concept="X",
        group_key="",
        group_label="",
        points=_points(values),
        horizon=5,
        persist=False,
    )
    assert result.status == "ok"
    assert result.model_used is not None
    assert len(result.predicted_points) == 5
    assert result.reliability in ("high", "medium", "low")