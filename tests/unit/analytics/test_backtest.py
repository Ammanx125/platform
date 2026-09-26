# tests/unit/analytics/test_backtest.py
from app.services.analytics.backtest import backtest


def test_backtest_picks_simpler_on_tie():
    # Constant series: naive, moving_average, exponential_smoothing all
    # produce exactly the constant. Ties go to naive (lowest complexity).
    values = [5.0] * 30
    scores = backtest(
        values,
        horizon=5,
        candidates=["naive", "moving_average", "exponential_smoothing"],
        metric="mae",
    )
    assert scores
    assert scores[0].model == "naive"


def test_backtest_picks_trend_model_for_trending_series():
    values = [float(i) for i in range(40)]
    scores = backtest(
        values,
        horizon=5,
        candidates=["naive", "moving_average", "exponential_smoothing"],
        metric="mae",
    )
    # Exponential smoothing should beat naive on a clean trend.
    assert scores
    assert scores[0].model == "exponential_smoothing"


def test_backtest_returns_empty_on_insufficient_data():
    scores = backtest(
        [1.0, 2.0, 3.0],
        horizon=5,
        candidates=["naive"],
        metric="mae",
    )
    assert scores == []