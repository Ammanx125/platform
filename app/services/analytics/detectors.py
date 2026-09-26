# app/services/analytics/detectors.py
"""
Anomaly detectors.

Each detector is a pure function: takes a TimeSeries, returns a list of
DetectedPoint. No DB access, no side effects. This makes them trivially
testable and easy to reason about.

Statistical parameters come from the detector definition's `parameters`
JSONB. Every detector accepts its parameters explicitly; the caller
(orchestrator in anomalies.py) is responsible for supplying them.

All detectors are deterministic. isolation_forest uses a fixed random_state.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.services.analytics.series import TimeSeries


class DetectorError(Exception):
    """Detector misconfigured or series unsuitable (too few points, etc.)."""


@dataclass
class DetectedPoint:
    """
    One anomalous point found by a detector. The orchestrator turns these
    into Anomaly rows.
    """
    timestamp: datetime
    value: float
    score: float              # magnitude of anomaly, higher = more anomalous
    expected_min: float | None
    expected_max: float | None
    detail: dict


# ---------- z-score global ----------

def detect_zscore_global(
    series: TimeSeries,
    *,
    k: float = 3.0,
) -> list[DetectedPoint]:
    """
    Flag points where |x - mean| > k * std.

    Sensible for stationary series. For trending series, use rolling.
    Requires >= 3 points; with fewer, the std is meaningless and we
    return no anomalies rather than producing garbage.
    """
    pts = series.points
    if len(pts) < 3:
        return []

    values = [p.value for p in pts]
    n = len(values)
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    std = var ** 0.5

    if std == 0:
        # Constant series — every point equals mean. Nothing is anomalous.
        return []

    expected_min = mean - k * std
    expected_max = mean + k * std

    out: list[DetectedPoint] = []
    for p in pts:
        z = (p.value - mean) / std
        if abs(z) > k:
            out.append(DetectedPoint(
                timestamp=p.timestamp,
                value=p.value,
                score=abs(z),
                expected_min=expected_min,
                expected_max=expected_max,
                detail={"mean": mean, "std": std, "zscore": z, "k": k},
            ))
    return out


# ---------- z-score rolling ----------

def detect_zscore_rolling(
    series: TimeSeries,
    *,
    k: float = 3.0,
    window: int = 7,
) -> list[DetectedPoint]:
    """
    Flag points where |x - rolling_mean(w)| > k * rolling_std(w).

    Handles trending series. The first `window` points have no full window
    behind them and are not evaluated.

    Each point is compared against the preceding window, so the current
    value cannot dilute its own baseline.
    """
    pts = series.points
    if len(pts) < window + 1:
        return []

    out: list[DetectedPoint] = []
    values = [p.value for p in pts]

    for i in range(window, len(values)):
        chunk = values[i - window : i]
        n = len(chunk)
        mean = sum(chunk) / n
        var = sum((v - mean) ** 2 for v in chunk) / (n - 1) if n > 1 else 0.0
        std = var ** 0.5
        if std == 0:
            continue
        v = values[i]
        z = (v - mean) / std
        if abs(z) > k:
            out.append(DetectedPoint(
                timestamp=pts[i].timestamp,
                value=v,
                score=abs(z),
                expected_min=mean - k * std,
                expected_max=mean + k * std,
                detail={"window": window, "mean": mean, "std": std, "zscore": z, "k": k},
            ))
    return out


# ---------- IQR ----------

def detect_iqr(
    series: TimeSeries,
    *,
    k: float = 1.5,
) -> list[DetectedPoint]:
    """
    Flag points outside [Q1 - k*IQR, Q3 + k*IQR].

    Robust to outliers in the "training" data — the presence of one
    extreme point does not inflate the fence the way std would.

    Requires >= 4 points for quartiles to be meaningful.
    """
    pts = series.points
    if len(pts) < 4:
        return []

    values_sorted = sorted(p.value for p in pts)
    q1 = _quantile(values_sorted, 0.25)
    q3 = _quantile(values_sorted, 0.75)
    iqr = q3 - q1
    if iqr == 0:
        return []

    lo = q1 - k * iqr
    hi = q3 + k * iqr

    out: list[DetectedPoint] = []
    for p in pts:
        if p.value < lo or p.value > hi:
            # Score: how many IQRs outside the fence.
            if p.value < lo:
                score = (lo - p.value) / iqr
            else:
                score = (p.value - hi) / iqr
            out.append(DetectedPoint(
                timestamp=p.timestamp,
                value=p.value,
                score=score,
                expected_min=lo,
                expected_max=hi,
                detail={"q1": q1, "q3": q3, "iqr": iqr, "k": k},
            ))
    return out


def _quantile(sorted_values: list[float], q: float) -> float:
    """Linear-interpolation quantile. Values are pre-sorted ascending."""
    if not sorted_values:
        raise DetectorError("empty series")
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    idx = q * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    frac = idx - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


# ---------- Isolation Forest ----------

_MIN_IF_POINTS = 50


def detect_isolation_forest(
    series: TimeSeries,
    *,
    n_estimators: int = 100,
    contamination: str | float = "auto",
) -> list[DetectedPoint]:
    """
    Flag points using sklearn's IsolationForest.

    Constraints:
      - Requires >= 50 points. Below that, the model can't distinguish
        signal from noise; we raise rather than produce garbage.
      - Uses random_state=42 for determinism.
      - expected_min / expected_max are None — IsolationForest produces a
        decision score, not a range.

    Score is -decision_function(x). Higher = more anomalous.
    """
    pts = series.points
    if len(pts) < _MIN_IF_POINTS:
        raise DetectorError(
            f"isolation_forest requires >= {_MIN_IF_POINTS} points, "
            f"series has {len(pts)}"
        )

    # Imported lazily so the module loads fast when IF isn't used.
    import numpy as np
    from sklearn.ensemble import IsolationForest

    values = np.array([p.value for p in pts], dtype=float).reshape(-1, 1)
    model = IsolationForest(
        n_estimators=n_estimators,
        contamination=contamination,
        random_state=42,
    )
    model.fit(values)
    preds = model.predict(values)           # -1 for anomalies, 1 for normal
    scores = -model.decision_function(values)   # higher = more anomalous

    out: list[DetectedPoint] = []
    for i, p in enumerate(pts):
        if preds[i] == -1:
            out.append(DetectedPoint(
                timestamp=p.timestamp,
                value=p.value,
                score=float(scores[i]),
                expected_min=None,
                expected_max=None,
                detail={
                    "n_estimators": n_estimators,
                    "contamination": contamination,
                    "raw_score": float(scores[i]),
                },
            ))
    return out


# ---------- registry ----------

DETECTORS = {
    "zscore_global": detect_zscore_global,
    "zscore_rolling": detect_zscore_rolling,
    "iqr": detect_iqr,
    "isolation_forest": detect_isolation_forest,
}


def get_detector(name: str):
    try:
        return DETECTORS[name]
    except KeyError as exc:
        raise DetectorError(
            f"unknown detector {name!r}; known: {sorted(DETECTORS)}"
        ) from exc