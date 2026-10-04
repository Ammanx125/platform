# tests/unit/analytics/test_forecasting.py
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services.analytics import forecasting
from app.services.analytics.forecasting import forecast_series
from app.services.analytics.series import TimeSeries, TimeSeriesPoint


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


@pytest.mark.asyncio
async def test_forecast_days_are_converted_to_steps_at_series_frequency():
    points = [
        TimeSeriesPoint(
            timestamp=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=i * 7),
            value=float(i),
            source_id=uuid.uuid4(),
        )
        for i in range(40)
    ]
    result = await forecast_series(
        _DBStub(),  # type: ignore[arg-type]
        tenant_id=uuid.uuid4(),
        value_concept="Finance.Revenue",
        group_key="",
        group_label="",
        points=points,
        horizon_days=30,
        persist=False,
    )

    assert result.status == "ok"
    assert result.horizon == 5
    assert len(result.predicted_points) == 5


@pytest.mark.asyncio
async def test_concept_forecast_passes_source_ids_from_each_series(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_a, source_b, source_c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    built_series = [
        TimeSeries(
            group_key="north",
            group_label="North",
            value_concept="revenue",
            group_by_concept="region",
            points=[
                TimeSeriesPoint(timestamp, 1.0, source_a),
                TimeSeriesPoint(timestamp + timedelta(days=1), 2.0, source_b),
                TimeSeriesPoint(timestamp + timedelta(days=2), 3.0, source_a),
            ],
        ),
        TimeSeries(
            group_key="south",
            group_label="South",
            value_concept="revenue",
            group_by_concept="region",
            points=[TimeSeriesPoint(timestamp, 4.0, source_c)],
        ),
    ]
    build_kwargs: dict = {}
    forecast_source_ids: list[list[str] | None] = []

    async def fake_build_series(_db, **kwargs):
        build_kwargs.update(kwargs)
        return SimpleNamespace(series=built_series)

    async def fake_forecast_series(_db, **kwargs):
        forecast_source_ids.append(kwargs["source_ids"])
        return kwargs["source_ids"]

    monkeypatch.setattr(forecasting, "build_series", fake_build_series)
    monkeypatch.setattr(forecasting, "forecast_series", fake_forecast_series)
    selected_sources = [source_a, source_b, source_c]

    results = await forecasting.run_forecast_for_concept(
        _DBStub(),  # type: ignore[arg-type]
        tenant_id=uuid.uuid4(),
        value_concept="revenue",
        group_by_concept="region",
        source_ids=selected_sources,
    )

    assert build_kwargs["source_ids"] == selected_sources
    assert forecast_source_ids == [
        [str(source_a), str(source_b)],
        [str(source_c)],
    ]
    assert results == forecast_source_ids