# app/services/analytics/forecasting.py
"""
Forecast orchestration: build series, detect frequency, generate
candidates, backtest, produce a forecast, persist, and expose list/read.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.analytics import Forecast
from app.services.analytics.backtest import backtest
from app.services.analytics.forecast_models import (
    ModelFitError,
    get_model,
)
from app.services.analytics.frequency import Frequency, detect_frequency
from app.services.analytics.series import TimeSeries, build_series


@dataclass
class PredictedPoint:
    timestamp: datetime
    value: float


@dataclass
class ForecastResult:
    """
    The output of one forecast run. Mirrors the persisted Forecast row but
    without DB-specific fields (id, tenant_id, created_at).
    """
    value_concept: str
    group_key: str
    group_label: str
    source_ids: list[str]

    horizon: int
    frequency: str
    has_seasonality: bool
    seasonality_period: int | None

    model_used: str | None
    candidates_evaluated: list[dict]     # [{model, error, metric, selected}]
    evaluation_metric: str
    validation_error: float | None

    predicted_points: list[dict]         # [{timestamp: iso, value: float}]
    lower_bound: list[dict]
    upper_bound: list[dict]

    reliability: str                     # high | medium | low | none
    status: str                          # ok | insufficient_history | failed
    notes: dict
    trained_at: datetime


def _generate_candidates(
    *,
    frequency: Frequency,
    series_len: int,
    requested: list[str] | None,
) -> list[str]:
    """
    Pick candidate models based on what's applicable.

    If `requested` is provided, respect it but still drop candidates that
    can't fit (like seasonal_naive without seasonality).
    """
    base = ["naive", "moving_average", "exponential_smoothing"]
    if frequency.has_seasonality and frequency.seasonality_period:
        base.append("seasonal_naive")

    if requested is not None:
        # Keep only requested ∩ applicable, preserving requested order.
        applicable = set(base)
        base = [m for m in requested if m in applicable]

    return base


def _compute_reliability(validation_error: float, series_mean: float) -> str:
    if series_mean == 0:
        return "low"
    rel = validation_error / abs(series_mean)
    if rel <= settings.forecast_reliability_high_max_rel_error:
        return "high"
    if rel <= settings.forecast_reliability_medium_max_rel_error:
        return "medium"
    return "low"


def _next_timestamps(
    last_ts: datetime, interval: str, median_gap_days: float, horizon: int
) -> list[datetime]:
    """Generate future timestamps by extending the series at its cadence."""
    if median_gap_days <= 0:
        # Fallback: assume 1-day spacing.
        delta = timedelta(days=1)
    else:
        delta = timedelta(days=median_gap_days)
    return [last_ts + delta * (i + 1) for i in range(horizon)]


async def forecast_series(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    value_concept: str,
    group_key: str,
    group_label: str,
    points: list,                       # list[TimeSeriesPoint]
    horizon: int | None = None,
    requested_models: list[str] | None = None,
    source_ids: list[str] | None = None,
    persist: bool = True,
) -> ForecastResult:
    """
    Core forecast function. Takes a pre-built series (points already
    assembled) and produces a ForecastResult. Persists if `persist=True`.
    """
    horizon = horizon or settings.forecast_default_horizon
    metric = settings.forecast_evaluation_metric

    series_obj = TimeSeries(
        group_key=group_key,
        group_label=group_label,
        value_concept=value_concept,
        group_by_concept=None,
        points=list(points),
    )

    frequency = detect_frequency(series_obj)
    values = [p.value for p in series_obj.points]
    trained_at = datetime.now(UTC)

    # Insufficient history → refuse.
    if len(values) < settings.forecast_min_points or not series_obj.points:
        result = ForecastResult(
            value_concept=value_concept,
            group_key=group_key,
            group_label=group_label,
            source_ids=source_ids or [],
            horizon=horizon,
            frequency=frequency.interval,
            has_seasonality=frequency.has_seasonality,
            seasonality_period=frequency.seasonality_period,
            model_used=None,
            candidates_evaluated=[],
            evaluation_metric=metric,
            validation_error=None,
            predicted_points=[],
            lower_bound=[],
            upper_bound=[],
            reliability="none",
            status="insufficient_history",
            notes={
                "reason": f"series has {len(values)} points, "
                          f"min is {settings.forecast_min_points}",
            },
            trained_at=trained_at,
        )
        if persist:
            await _persist(db, tenant_id=tenant_id, result=result)
        return result

    # Generate candidates.
    candidates = _generate_candidates(
        frequency=frequency,
        series_len=len(values),
        requested=requested_models,
    )

    # Model parameters derived from frequency.
    model_params: dict[str, dict] = {
        "moving_average": {"window": min(settings.forecast_moving_average_window, len(values))},
    }
    if frequency.seasonality_period:
        model_params["seasonal_naive"] = {"period": frequency.seasonality_period}
        model_params["exponential_smoothing"] = {
            "seasonal_period": frequency.seasonality_period,
        }

    # Backtest.
    scores = backtest(
        values,
        horizon=horizon,
        candidates=candidates,
        metric=metric,
        model_params=model_params,
    )

    if not scores:
        # Every candidate failed — fall back to naive explicitly.
        try:
            model_fn = get_model("naive")
            output = model_fn(values, horizon)
            selected = "naive"
            validation_error = None
            candidates_eval: list[dict] = []
        except ModelFitError as exc:
            result = ForecastResult(
                value_concept=value_concept,
                group_key=group_key,
                group_label=group_label,
                source_ids=source_ids or [],
                horizon=horizon,
                frequency=frequency.interval,
                has_seasonality=frequency.has_seasonality,
                seasonality_period=frequency.seasonality_period,
                model_used=None,
                candidates_evaluated=[],
                evaluation_metric=metric,
                validation_error=None,
                predicted_points=[],
                lower_bound=[],
                upper_bound=[],
                reliability="none",
                status="failed",
                notes={"reason": f"all candidates failed; naive failed too: {exc}"},
                trained_at=trained_at,
            )
            if persist:
                await _persist(db, tenant_id=tenant_id, result=result)
            return result
    else:
        selected = scores[0].model
        validation_error = scores[0].error
        candidates_eval = [
            {
                "model": s.model,
                "error": s.error,
                "metric": s.metric,
                "selected": s.model == selected,
            }
            for s in scores
        ]
        model_fn = get_model(selected)
        params = model_params.get(selected, {})
        output = model_fn(values, horizon, **params)

    # Build predicted points with timestamps.
    last_ts = series_obj.points[-1].timestamp
    future_ts = _next_timestamps(last_ts, frequency.interval, frequency.median_gap_days, horizon)

    predicted_points = [
        {"timestamp": future_ts[i].isoformat(), "value": float(output.predicted[i])}
        for i in range(horizon)
    ]
    lower_bound = [
        {"timestamp": future_ts[i].isoformat(), "value": float(output.lower[i])}
        for i in range(min(horizon, len(output.lower)))
    ]
    upper_bound = [
        {"timestamp": future_ts[i].isoformat(), "value": float(output.upper[i])}
        for i in range(min(horizon, len(output.upper)))
    ]

    series_mean = sum(values) / len(values)
    reliability = (
        _compute_reliability(validation_error, series_mean)
        if validation_error is not None
        else "low"
    )

    result = ForecastResult(
        value_concept=value_concept,
        group_key=group_key,
        group_label=group_label,
        source_ids=source_ids or [],
        horizon=horizon,
        frequency=frequency.interval,
        has_seasonality=frequency.has_seasonality,
        seasonality_period=frequency.seasonality_period,
        model_used=selected,
        candidates_evaluated=candidates_eval,
        evaluation_metric=metric,
        validation_error=validation_error,
        predicted_points=predicted_points,
        lower_bound=lower_bound,
        upper_bound=upper_bound,
        reliability=reliability,
        status="ok",
        notes={
            "median_gap_days": frequency.median_gap_days,
            "series_length": len(values),
        },
        trained_at=trained_at,
    )
    if persist:
        await _persist(db, tenant_id=tenant_id, result=result)
    return result


async def run_forecast_for_concept(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    value_concept: str,
    group_by_concept: str | None,
    horizon: int | None = None,
    source_ids: list[uuid.UUID] | None = None,
    requested_models: list[str] | None = None,
) -> list[ForecastResult]:
    """
    Build series from staged data and forecast each group.
    """
    build = await build_series(
        db,
        tenant_id=tenant_id,
        source_ids=source_ids,
        value_concept=value_concept,
        group_by_concept=group_by_concept,
    )
    results: list[ForecastResult] = []
    for s in build.series:
        result = await forecast_series(
            db,
            tenant_id=tenant_id,
            value_concept=value_concept,
            group_key=s.group_key,
            group_label=s.group_label,
            points=s.points,
            horizon=horizon,
            requested_models=requested_models,
            source_ids=[str(sid) for sid in build.series[0].points[0].source_id.__class__.__mro__[:0]] or [],
        )
        results.append(result)
    return results


async def _persist(
    db: AsyncSession, *, tenant_id: uuid.UUID, result: ForecastResult
) -> Forecast:
    row = Forecast(
        tenant_id=tenant_id,
        value_concept=result.value_concept,
        group_key=result.group_key,
        group_label=result.group_label,
        source_ids=result.source_ids,
        horizon=result.horizon,
        frequency=result.frequency,
        has_seasonality=result.has_seasonality,
        seasonality_period=result.seasonality_period,
        model_used=result.model_used,
        candidates_evaluated=result.candidates_evaluated,
        evaluation_metric=result.evaluation_metric,
        validation_error=result.validation_error,
        predicted_points=result.predicted_points,
        lower_bound=result.lower_bound,
        upper_bound=result.upper_bound,
        reliability=result.reliability,
        status=result.status,
        notes=result.notes,
        trained_at=result.trained_at,
    )
    db.add(row)
    await db.flush()
    return row


async def list_forecasts(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    value_concept: str | None = None,
    group_key: str | None = None,
    since: datetime | None = None,
    limit: int = 100,
) -> list[Forecast]:
    stmt = (
        select(Forecast)
        .where(Forecast.tenant_id == tenant_id)
        .order_by(Forecast.trained_at.desc())
        .limit(limit)
    )
    if value_concept is not None:
        stmt = stmt.where(Forecast.value_concept == value_concept)
    if group_key is not None:
        stmt = stmt.where(Forecast.group_key == group_key)
    if since is not None:
        stmt = stmt.where(Forecast.trained_at >= since)
    return list((await db.execute(stmt)).scalars().all())


async def get_forecast(
    db: AsyncSession, *, forecast_id: uuid.UUID, tenant_id: uuid.UUID
) -> Forecast | None:
    return (
        await db.execute(
            select(Forecast).where(
                Forecast.id == forecast_id,
                Forecast.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()