# tests/unit/analytics/test_forecast_models.py
import pytest

from app.services.analytics.forecast_models import (
    ModelFitError,
    fit_exponential_smoothing,
    fit_moving_average,
    fit_naive,
    fit_seasonal_naive,
)


def test_naive_repeats_last():
    out = fit_naive([1.0, 2.0, 3.0], 3)
    assert out.predicted == [3.0, 3.0, 3.0]


def test_naive_requires_data():
    with pytest.raises(ModelFitError):
        fit_naive([], 3)


def test_seasonal_naive_repeats_period():
    # period 3, values [1, 2, 3, 4, 5, 6] -> last period is [4, 5, 6]
    out = fit_seasonal_naive([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], 4, period=3)
    assert out.predicted == [4.0, 5.0, 6.0, 4.0]


def test_seasonal_naive_rejects_short_series():
    with pytest.raises(ModelFitError):
        fit_seasonal_naive([1.0, 2.0], 3, period=5)


def test_moving_average():
    out = fit_moving_average([1.0, 2.0, 3.0, 4.0], 3, window=3)
    # mean of last 3 = (2+3+4)/3 = 3
    assert out.predicted == [3.0, 3.0, 3.0]


def test_exponential_smoothing_on_trend():
    values = [float(i) for i in range(20)]
    out = fit_exponential_smoothing(values, 5)
    # Additive trend should extend upward.
    assert out.predicted[0] > 18.0
    assert out.predicted[-1] > out.predicted[0]


def test_exponential_smoothing_requires_min_points():
    with pytest.raises(ModelFitError):
        fit_exponential_smoothing([1.0, 2.0], 3)