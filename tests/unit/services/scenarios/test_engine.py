# tests/unit/services/scenarios/test_engine.py
"""
The engine is the one part of Scenario Lab that must be provably right.
These tests use hand-calculated numbers only: no database, no async, no
fixtures beyond the module imports.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.scenarios import engine


def _suppliers(*pairs: tuple[str, str]) -> list[engine.SupplierSpend]:
    return [
        engine.SupplierSpend(supplier_key=name, baseline_spend=Decimal(amount))
        for name, amount in pairs
    ]


def test_reference_case_matches_hand_calculation():
    """2,000,000 at +12% -> 2,240,000, an increase of 240,000."""
    result = engine.simulate_supplier_spend(
        suppliers=_suppliers(("A", "1200000"), ("B", "800000")),
        price_change_pct=Decimal("12"),
    )
    assert result.baseline_spend == Decimal("2000000.00")
    assert result.projected_spend == Decimal("2240000.00")
    assert result.increase == Decimal("240000.00")
    assert result.increase_pct == Decimal("12.00")


def test_supplier_contributions_sum_to_total_increase():
    """One supplier's share must equal its increase / total increase."""
    result = engine.simulate_supplier_spend(
        suppliers=_suppliers(("A", "100.00"), ("B", "300.00"), ("C", "600.00")),
        price_change_pct=Decimal("10"),
    )
    total = sum(s.increase for s in result.suppliers)
    assert total == result.increase
    # Ranking is by absolute increase, largest first.
    assert [s.supplier_key for s in result.suppliers] == ["C", "B", "A"]
    assert result.suppliers[0].increase == Decimal("60.00")
    # 60 / 100 = 60% of the total increase.
    assert result.suppliers[0].share_of_total_increase_pct == Decimal("60.00")
    assert result.suppliers[1].share_of_total_increase_pct == Decimal("30.00")


def test_shares_sum_to_one_hundred_within_rounding():
    """Per-supplier shares are rounded for display, so allow a cent of drift."""
    result = engine.simulate_supplier_spend(
        suppliers=_suppliers(
            ("A", "100.00"), ("B", "300.10"), ("C", "600.30"), ("D", "77.77")
        ),
        price_change_pct=Decimal("12"),
    )
    total_share = sum(s.share_of_total_increase_pct for s in result.suppliers)
    assert abs(total_share - Decimal("100")) <= Decimal("0.05")


def test_deterministic_across_calls():
    first = engine.simulate_supplier_spend(
        suppliers=_suppliers(("A", "10.005"), ("B", "20.015")),
        price_change_pct=Decimal("7.5"),
    )
    second = engine.simulate_supplier_spend(
        suppliers=_suppliers(("A", "10.005"), ("B", "20.015")),
        price_change_pct=Decimal("7.5"),
    )
    assert first == second


def test_price_reduction_produces_negative_increase_without_ranking():
    result = engine.simulate_supplier_spend(
        suppliers=_suppliers(("A", "1000")),
        price_change_pct=Decimal("-10"),
    )
    assert result.projected_spend == Decimal("900.00")
    assert result.increase == Decimal("-100.00")
    assert result.increase_pct == Decimal("-10.00")
    assert result.suppliers[0].share_of_total_increase_pct is None


def test_zero_baseline_has_undefined_percentage():
    result = engine.simulate_supplier_spend(
        suppliers=_suppliers(("A", "0")),
        price_change_pct=Decimal("10"),
    )
    assert result.increase_pct is None
    assert result.increase == Decimal("0.00")


def test_larger_price_decrease_never_increases_spend():
    """Property: holding the mix fixed, a more negative change is never higher."""
    base = _suppliers(("A", "500"), ("B", "1500"))
    previous = None
    for step in (Decimal("40"), Decimal("10"), Decimal("0"), Decimal("-10"), Decimal("-40")):
        current = engine.simulate_supplier_spend(
            suppliers=base, price_change_pct=step
        ).projected_spend
        if previous is not None:
            assert current <= previous
        previous = current


def test_higher_cost_never_increases_spend():
    """Property: with fixed volume, raising the price cannot lower spend."""
    base = _suppliers(("A", "800"), ("B", "200"))
    amounts = [
        engine.simulate_supplier_spend(
            suppliers=base, price_change_pct=Decimal(step)
        ).increase
        for step in ("0", "1", "5", "20", "100")
    ]
    assert amounts == sorted(amounts)


@pytest.mark.parametrize(
    "change",
    [Decimal("-100"), Decimal("-150")],
)
def test_price_level_at_or_below_zero_is_rejected(change):
    with pytest.raises(engine.ScenarioCalculationError):
        engine.simulate_supplier_spend(suppliers=_suppliers(("A", "1")), price_change_pct=change)


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_non_finite_inputs_are_rejected(value):
    with pytest.raises(engine.ScenarioCalculationError):
        engine.simulate_supplier_spend(
            suppliers=_suppliers(("A", "1")), price_change_pct=value
        )
    with pytest.raises(engine.ScenarioCalculationError):
        engine.simulate_supplier_spend(
            suppliers=[
                engine.SupplierSpend(supplier_key="A", baseline_spend=value)
            ],
            price_change_pct=Decimal("10"),
        )


def test_negative_baseline_spend_is_rejected():
    with pytest.raises(engine.ScenarioCalculationError):
        engine.simulate_supplier_spend(
            suppliers=_suppliers(("A", "-5")), price_change_pct=Decimal("10")
        )


def test_empty_supplier_list_is_rejected():
    with pytest.raises(engine.ScenarioCalculationError):
        engine.simulate_supplier_spend(suppliers=[], price_change_pct=Decimal("10"))


def test_no_binary_float_drift_on_awkward_values():
    """0.1 + 0.2 style values must stay exact under Decimal."""
    result = engine.simulate_supplier_spend(
        suppliers=_suppliers(("A", "0.1"), ("B", "0.2")),
        price_change_pct=Decimal("0"),
    )
    assert result.baseline_spend == Decimal("0.30")


def test_explanation_names_the_largest_driver():
    result = engine.simulate_supplier_spend(
        suppliers=_suppliers(("Big Supplier", "900"), ("Small Supplier", "100")),
        price_change_pct=Decimal("10"),
    )
    text = engine.explain_deterministically(result)
    assert "Big Supplier" in text
    assert "Small Supplier" in text
    assert "1,000.00" in text


def test_explanation_handles_zero_baseline():
    result = engine.simulate_supplier_spend(
        suppliers=_suppliers(("A", "0")), price_change_pct=Decimal("10")
    )
    assert "undefined" in engine.explain_deterministically(result)
