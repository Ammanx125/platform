# app/services/analytics/frequency.py
"""
Frequency and seasonality detection.

Given a series' timestamps, infer:
  - interval: 'daily' | 'weekly' | 'monthly' | 'irregular'
  - median_gap_days: float
  - has_seasonality: bool
  - seasonality_period: int | None (in units of the inferred interval)

Seasonality detection uses autocorrelation on the value series. The
strongest ACF peak within a plausible lag range becomes the candidate
period; if it exceeds the configured threshold, seasonality is declared.

Irregular series are treated as having no seasonality, because ACF assumes
evenly-spaced observations.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings
from app.services.analytics.series import TimeSeries


@dataclass
class Frequency:
    interval: str
    median_gap_days: float
    has_seasonality: bool
    seasonality_period: int | None


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2.0


def _classify_interval(median_gap_days: float) -> str:
    if median_gap_days <= 0:
        return "irregular"
    if median_gap_days < 2:
        return "daily"
    if median_gap_days < 10:
        return "weekly"
    if median_gap_days < 45:
        return "monthly"
    return "irregular"


def _autocorrelation(values: list[float], lag: int) -> float:
    """
    Pearson autocorrelation of `values` at the given lag. Returns 0 if the
    series is too short or has zero variance.
    """
    n = len(values)
    if lag <= 0 or lag >= n:
        return 0.0
    mean = sum(values) / n
    denom = sum((v - mean) ** 2 for v in values)
    if denom == 0:
        return 0.0
    num = sum((values[i] - mean) * (values[i - lag] - mean) for i in range(lag, n))
    return num / denom


def detect_frequency(series: TimeSeries) -> Frequency:
    """
    Infer interval + seasonality from a series.

    Empty / single-point series: interval='irregular', no seasonality.
    """
    pts = series.points
    if len(pts) < 2:
        return Frequency(
            interval="irregular",
            median_gap_days=0.0,
            has_seasonality=False,
            seasonality_period=None,
        )

    gaps_days = [
        (pts[i].timestamp - pts[i - 1].timestamp).total_seconds() / 86400.0
        for i in range(1, len(pts))
    ]
    median_gap = _median(gaps_days)
    interval = _classify_interval(median_gap)

    if interval == "irregular":
        return Frequency(
            interval=interval,
            median_gap_days=median_gap,
            has_seasonality=False,
            seasonality_period=None,
        )

    values = [p.value for p in pts]
    n = len(values)
    min_period = settings.forecast_seasonality_min_period
    max_period = max(min_period, int(n * settings.forecast_seasonality_max_fraction))

    if max_period < min_period or n < 2 * min_period:
        return Frequency(
            interval=interval,
            median_gap_days=median_gap,
            has_seasonality=False,
            seasonality_period=None,
        )

    best_lag = None
    best_acf = 0.0
    for lag in range(min_period, max_period + 1):
        acf = _autocorrelation(values, lag)
        if acf > best_acf:
            best_acf = acf
            best_lag = lag

    if best_lag is None or best_acf < settings.forecast_acf_peak_threshold:
        return Frequency(
            interval=interval,
            median_gap_days=median_gap,
            has_seasonality=False,
            seasonality_period=None,
        )

    return Frequency(
        interval=interval,
        median_gap_days=median_gap,
        has_seasonality=True,
        seasonality_period=best_lag,
    )