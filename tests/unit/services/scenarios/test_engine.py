"""Hand-checked correctness tests for the pure profit sensitivity engine."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.scenarios import engine


def _baseline(revenue: str = "1000000", cogs: str = "700000") -> engine.ProfitBaseline:
    return engine.ProfitBaseline(revenue=Decimal(revenue), cogs=Decimal(cogs), currency="USD")


def _assumptions(
    supplier_cost: str = "12",
    selling_price: str = "0",
    volume: str = "0",
) -> engine.ProfitAssumptions:
    return engine.ProfitAssumptions(
        supplier_cost_change_pct=Decimal(supplier_cost),
        selling_price_change_pct=Decimal(selling_price),
        volume_change_pct=Decimal(volume),
    )


def test_reference_case_matches_original_plan_hand_calculation():
    result = engine.simulate_profit(
        baseline=_baseline(),
        assumptions=_assumptions(supplier_cost="12"),
    )

    assert result.baseline_revenue == Decimal("1000000.00")
    assert result.baseline_cogs == Decimal("700000.00")
    assert result.baseline_gross_profit == Decimal("300000.00")
    assert result.baseline_gross_margin_pct == Decimal("30.00")
    assert result.scenario_revenue == Decimal("1000000.00")
    assert result.scenario_cogs == Decimal("784000.00")
    assert result.scenario_gross_profit == Decimal("216000.00")
    assert result.scenario_gross_margin_pct == Decimal("21.60")
    assert result.gross_profit_change == Decimal("-84000.00")
    assert result.gross_profit_change_pct == Decimal("-28.00")
    assert result.gross_margin_change_percentage_points == Decimal("-8.40")


def test_selling_price_and_volume_affect_revenue_and_cogs_as_specified():
    result = engine.simulate_profit(
        baseline=_baseline(),
        assumptions=_assumptions(supplier_cost="12", selling_price="10", volume="-10"),
    )

    assert result.scenario_revenue == Decimal("990000.00")
    assert result.scenario_cogs == Decimal("705600.00")
    assert result.scenario_gross_profit == Decimal("284400.00")
    assert result.scenario_gross_margin_pct == Decimal("28.73")


def test_revenue_and_cogs_scale_proportionally_with_volume():
    result = engine.simulate_profit(
        baseline=_baseline(revenue="100", cogs="70"),
        assumptions=_assumptions(supplier_cost="0", selling_price="0", volume="20"),
    )

    assert result.scenario_revenue == Decimal("120.00")
    assert result.scenario_cogs == Decimal("84.00")
    assert result.scenario_gross_profit == Decimal("36.00")
    assert result.scenario_gross_margin_pct == Decimal("30.00")


def test_deterministic_across_calls():
    args = {
        "baseline": _baseline(revenue="0.3", cogs="0.2"),
        "assumptions": _assumptions("7.5", "-2.5", "1.25"),
    }
    assert engine.simulate_profit(**args) == engine.simulate_profit(**args)


def test_zero_revenue_has_undefined_margin_and_zero_profit_delta_pct():
    result = engine.simulate_profit(
        baseline=_baseline(revenue="0", cogs="0"),
        assumptions=_assumptions(),
    )

    assert result.baseline_gross_margin_pct is None
    assert result.scenario_gross_margin_pct is None
    assert result.gross_profit_change_pct is None
    assert result.gross_margin_change_percentage_points is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("supplier_cost_change_pct", Decimal("-0.01")),
        ("supplier_cost_change_pct", Decimal("30.01")),
        ("selling_price_change_pct", Decimal("-10.01")),
        ("selling_price_change_pct", Decimal("20.01")),
        ("volume_change_pct", Decimal("-30.01")),
        ("volume_change_pct", Decimal("30.01")),
    ],
)
def test_assumptions_outside_policy_bounds_are_rejected(field, value):
    assumptions = _assumptions()
    assumptions = engine.ProfitAssumptions(**{**assumptions.__dict__, field: value})

    with pytest.raises(engine.ScenarioCalculationError):
        engine.simulate_profit(baseline=_baseline(), assumptions=assumptions)


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_non_finite_values_are_rejected(value):
    with pytest.raises(engine.ScenarioCalculationError):
        engine.simulate_profit(
            baseline=_baseline(),
            assumptions=engine.ProfitAssumptions(value, Decimal("0"), Decimal("0")),
        )
    with pytest.raises(engine.ScenarioCalculationError):
        engine.simulate_profit(baseline=_baseline(revenue=str(value)), assumptions=_assumptions())


@pytest.mark.parametrize(
    ("revenue", "cogs", "currency"),
    [
        (Decimal("-1"), Decimal("1"), "USD"),
        (Decimal("1"), Decimal("-1"), "USD"),
        (Decimal("1"), Decimal("1"), ""),
    ],
)
def test_invalid_baseline_values_are_rejected(revenue, cogs, currency):
    with pytest.raises(engine.ScenarioCalculationError):
        engine.simulate_profit(
            baseline=engine.ProfitBaseline(revenue, cogs, currency),
            assumptions=_assumptions(),
        )


def test_deterministic_explanation_uses_calculated_profit_change():
    result = engine.simulate_profit(
        baseline=_baseline(),
        assumptions=_assumptions(supplier_cost="12"),
    )

    assert "USD 84,000.00" in result.explanation
    assert "USD 300,000.00 to USD 216,000.00" in result.explanation
    assert "-8.40 percentage points" in result.explanation
    assert "forecast" in " ".join(result.limitations)
