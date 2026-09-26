# app/services/analytics/backtest.py
"""
Holdout backtest: reserve the final `horizon` observations, fit each
candidate on the preceding data, score on the held-out set.

Returns a list of CandidateScore sorted ascending by error. Candidates
that fail to fit are excluded (not returned).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.services.analytics.forecast_models import (
    MODEL_COMPLEXITY,
    ModelFitError,
    get_model,
)


@dataclass
class CandidateScore:
    model: str
    error: float
    metric: str
    fit_params: dict


def _mae(actual: list[float], predicted: list[float]) -> float:
    n = min(len(actual), len(predicted))
    if n == 0:
        return float("inf")
    return sum(abs(actual[i] - predicted[i]) for i in range(n)) / n


def _rmse(actual: list[float], predicted: list[float]) -> float:
    n = min(len(actual), len(predicted))
    if n == 0:
        return float("inf")
    return (sum((actual[i] - predicted[i]) ** 2 for i in range(n)) / n) ** 0.5


def _mape(actual: list[float], predicted: list[float]) -> float:
    n = min(len(actual), len(predicted))
    if n == 0:
        return float("inf")
    total = 0.0
    for i in range(n):
        if actual[i] == 0:
            # Undefined; skip. If all actuals are 0, MAPE is meaningless.
            continue
        total += abs((actual[i] - predicted[i]) / actual[i])
    return (total / n) * 100


_METRICS = {"mae": _mae, "rmse": _rmse, "mape": _mape}


def compute_error(metric: str, actual: list[float], predicted: list[float]) -> float:
    fn = _METRICS.get(metric)
    if fn is None:
        raise ValueError(f"unknown metric: {metric!r}; known: {sorted(_METRICS)}")
    return fn(actual, predicted)


def backtest(
    values: list[float],
    *,
    horizon: int,
    candidates: list[str],
    metric: str,
    model_params: dict[str, dict] | None = None,
) -> list[CandidateScore]:
    """
    Backtest each candidate. Returns CandidateScore list sorted ascending
    by error, with ties broken by preferring the simpler model.

    Candidates that fail to fit are silently excluded. If none survive,
    returns an empty list — the caller decides what to do.
    """
    if len(values) < 2 * horizon:
        # Not enough to hold out. Refuse cleanly.
        return []

    train = values[: len(values) - horizon]
    test = values[len(values) - horizon :]

    params_by_model = model_params or {}
    scores: list[CandidateScore] = []

    for name in candidates:
        try:
            model_fn = get_model(name)
            params = params_by_model.get(name, {})
            output = model_fn(train, horizon, **params)
        except ModelFitError:
            continue
        except Exception:  # noqa: BLE001
            # Any unhandled model error → excluded. We don't fail the whole
            # backtest because one candidate is broken.
            continue

        error = compute_error(metric, test, output.predicted)
        if error != error:  # NaN check
            continue
        scores.append(CandidateScore(
            model=name,
            error=error,
            metric=metric,
            fit_params=output.fit_params,
        ))

    # Sort by error, then by complexity (simpler wins).
    scores.sort(key=lambda s: (s.error, MODEL_COMPLEXITY.get(s.model, 99)))
    return scores