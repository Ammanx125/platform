# app/services/analytics/forecast_models.py
"""
Candidate forecast models.

Each model is a pure function:
    fit_and_forecast(values, horizon, **params) -> ForecastOutput

ForecastOutput carries predicted values plus optional lower/upper bounds.
Models that don't produce bounds leave them None (parallel empty lists).

Models raise ModelFitError if they can't be fitted (insufficient data,
degenerate series). The caller excludes failing candidates and continues.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


class ModelFitError(Exception):
    """Model cannot be fitted to this series."""


@dataclass
class ForecastOutput:
    predicted: list[float]
    lower: list[float] = field(default_factory=list)   # empty if none
    upper: list[float] = field(default_factory=list)
    fit_params: dict[str, Any] = field(default_factory=dict)


# ---------- naive ----------

def fit_naive(values: list[float], horizon: int, **_: Any) -> ForecastOutput:
    if not values:
        raise ModelFitError("naive: no data")
    return ForecastOutput(predicted=[values[-1]] * horizon)


# ---------- seasonal naive ----------

def fit_seasonal_naive(
    values: list[float], horizon: int, *, period: int, **_: Any
) -> ForecastOutput:
    if not values:
        raise ModelFitError("seasonal_naive: no data")
    if period <= 0:
        raise ModelFitError("seasonal_naive: period must be positive")
    if len(values) < period:
        raise ModelFitError("seasonal_naive: series shorter than period")
    preds: list[float] = []
    for i in range(horizon):
        idx = len(values) - period + (i % period)
        preds.append(values[idx])
    return ForecastOutput(predicted=preds, fit_params={"period": period})


# ---------- moving average ----------

def fit_moving_average(
    values: list[float], horizon: int, *, window: int = 7, **_: Any
) -> ForecastOutput:
    if not values:
        raise ModelFitError("moving_average: no data")
    w = min(window, len(values))
    if w < 1:
        raise ModelFitError("moving_average: no usable window")
    avg = sum(values[-w:]) / w
    return ForecastOutput(predicted=[avg] * horizon, fit_params={"window": w})


# ---------- exponential smoothing (Holt-Winters) ----------

def fit_exponential_smoothing(
    values: list[float], horizon: int, *, seasonal_period: int | None = None, **_: Any
) -> ForecastOutput:
    if len(values) < 3:
        raise ModelFitError("exponential_smoothing: needs >= 3 points")

    from statsmodels.tsa.holtwinters import ExponentialSmoothing

    seasonal = None
    if seasonal_period and seasonal_period >= 2 and len(values) >= 2 * seasonal_period:
        seasonal = "additive"

    try:
        model = ExponentialSmoothing(
            values,
            trend="add",
            seasonal=seasonal,
            seasonal_periods=seasonal_period if seasonal else None,
            initialization_method="estimated",
        )
        fit = model.fit(optimized=True)
        forecast = fit.forecast(horizon)
    except Exception as exc:  # noqa: BLE001
        raise ModelFitError(f"exponential_smoothing: fit failed: {exc}") from exc

    preds = [float(x) for x in forecast]
    return ForecastOutput(
        predicted=preds,
        fit_params={"seasonal": bool(seasonal), "seasonal_periods": seasonal_period},
    )


# ---------- registry ----------

MODELS: dict[str, Callable[..., ForecastOutput]] = {
    "naive": fit_naive,
    "seasonal_naive": fit_seasonal_naive,
    "moving_average": fit_moving_average,
    "exponential_smoothing": fit_exponential_smoothing,
}

# Complexity ranking, used as a tiebreaker. Lower = simpler.
MODEL_COMPLEXITY = {
    "naive": 0,
    "seasonal_naive": 1,
    "moving_average": 2,
    "exponential_smoothing": 3,
}


def get_model(name: str):
    try:
        return MODELS[name]
    except KeyError as exc:
        raise ModelFitError(f"unknown model: {name!r}") from exc