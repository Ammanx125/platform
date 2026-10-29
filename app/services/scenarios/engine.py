"""
Deterministic procurement and gross-profit sensitivity calculations.

Pure functions only: no database, filesystem, network, or LLM access.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, localcontext

SCENARIO_TYPE = "profit_sensitivity_v1"
FORMULA_VERSION = 1

SUPPLIER_COST_CHANGE_MIN = Decimal("0")
SUPPLIER_COST_CHANGE_MAX = Decimal("30")
SELLING_PRICE_CHANGE_MIN = Decimal("-10")
SELLING_PRICE_CHANGE_MAX = Decimal("20")
VOLUME_CHANGE_MIN = Decimal("-30")
VOLUME_CHANGE_MAX = Decimal("30")

_MONEY_QUANT = Decimal("0.01")
_PCT_QUANT = Decimal("0.01")


class ScenarioCalculationError(Exception):
    """Inputs are individually well-formed but the calculation is undefined."""


@dataclass(frozen=True)
class ProfitBaseline:
    revenue: Decimal
    cogs: Decimal
    currency: str


@dataclass(frozen=True)
class ProfitAssumptions:
    supplier_cost_change_pct: Decimal
    selling_price_change_pct: Decimal
    volume_change_pct: Decimal


@dataclass
class ProfitSensitivityResult:
    scenario_type: str
    formula_version: int
    currency: str
    assumptions: ProfitAssumptions
    baseline_revenue: Decimal
    baseline_cogs: Decimal
    baseline_gross_profit: Decimal
    baseline_gross_margin_pct: Decimal | None
    scenario_revenue: Decimal
    scenario_cogs: Decimal
    scenario_gross_profit: Decimal
    scenario_gross_margin_pct: Decimal | None
    gross_profit_change: Decimal
    gross_profit_change_pct: Decimal | None
    gross_margin_change_percentage_points: Decimal | None
    explanation: str
    limitations: list[str] = field(default_factory=list)


def _rounded(value: Decimal, quant: Decimal) -> Decimal:
    return value.quantize(quant, rounding=ROUND_HALF_UP)


def _require_finite(value: Decimal, field_name: str) -> None:
    if not value.is_finite():
        raise ScenarioCalculationError(f"{field_name} must be a finite number")


def _validate_assumption(value: Decimal, *, name: str, minimum: Decimal, maximum: Decimal) -> None:
    _require_finite(value, name)
    if not minimum <= value <= maximum:
        raise ScenarioCalculationError(f"{name} must be between {minimum} and {maximum} percent")


def simulate_profit(
    *,
    baseline: ProfitBaseline,
    assumptions: ProfitAssumptions,
) -> ProfitSensitivityResult:
    """
    Calculate the gross-profit impact while holding the purchasing mix fixed.

    Both revenue and COGS scale with volume. Selling price affects revenue;
    supplier cost affects COGS. This is sensitivity analysis, not a forecast.
    """
    _require_finite(baseline.revenue, "baseline.revenue")
    _require_finite(baseline.cogs, "baseline.cogs")
    if baseline.revenue < 0:
        raise ScenarioCalculationError("baseline revenue must be non-negative")
    if baseline.cogs < 0:
        raise ScenarioCalculationError("baseline COGS must be non-negative")
    if not baseline.currency or baseline.currency != baseline.currency.strip():
        raise ScenarioCalculationError("baseline currency must be a normalized code")

    _validate_assumption(
        assumptions.supplier_cost_change_pct,
        name="supplier_cost_change_pct",
        minimum=SUPPLIER_COST_CHANGE_MIN,
        maximum=SUPPLIER_COST_CHANGE_MAX,
    )
    _validate_assumption(
        assumptions.selling_price_change_pct,
        name="selling_price_change_pct",
        minimum=SELLING_PRICE_CHANGE_MIN,
        maximum=SELLING_PRICE_CHANGE_MAX,
    )
    _validate_assumption(
        assumptions.volume_change_pct,
        name="volume_change_pct",
        minimum=VOLUME_CHANGE_MIN,
        maximum=VOLUME_CHANGE_MAX,
    )

    with localcontext() as context:
        context.prec = 40
        volume_factor = Decimal(1) + assumptions.volume_change_pct / Decimal(100)
        revenue_factor = Decimal(1) + assumptions.selling_price_change_pct / Decimal(100)
        cogs_factor = Decimal(1) + assumptions.supplier_cost_change_pct / Decimal(100)

        baseline_revenue = _rounded(baseline.revenue, _MONEY_QUANT)
        baseline_cogs = _rounded(baseline.cogs, _MONEY_QUANT)
        baseline_profit = _rounded(baseline_revenue - baseline_cogs, _MONEY_QUANT)
        scenario_revenue = _rounded(baseline.revenue * revenue_factor * volume_factor, _MONEY_QUANT)
        scenario_cogs = _rounded(baseline.cogs * cogs_factor * volume_factor, _MONEY_QUANT)
        scenario_profit = _rounded(scenario_revenue - scenario_cogs, _MONEY_QUANT)
        profit_change = _rounded(scenario_profit - baseline_profit, _MONEY_QUANT)

        baseline_margin = (
            _rounded(baseline_profit / baseline_revenue * Decimal(100), _PCT_QUANT)
            if baseline_revenue > 0
            else None
        )
        scenario_margin = (
            _rounded(scenario_profit / scenario_revenue * Decimal(100), _PCT_QUANT)
            if scenario_revenue > 0
            else None
        )
        profit_change_pct = (
            _rounded(profit_change / abs(baseline_profit) * Decimal(100), _PCT_QUANT)
            if baseline_profit != 0
            else None
        )
        margin_change = (
            _rounded(scenario_margin - baseline_margin, _PCT_QUANT)
            if scenario_margin is not None and baseline_margin is not None
            else None
        )

    result = ProfitSensitivityResult(
        scenario_type=SCENARIO_TYPE,
        formula_version=FORMULA_VERSION,
        currency=baseline.currency,
        assumptions=assumptions,
        baseline_revenue=baseline_revenue,
        baseline_cogs=baseline_cogs,
        baseline_gross_profit=baseline_profit,
        baseline_gross_margin_pct=baseline_margin,
        scenario_revenue=scenario_revenue,
        scenario_cogs=scenario_cogs,
        scenario_gross_profit=scenario_profit,
        scenario_gross_margin_pct=scenario_margin,
        gross_profit_change=profit_change,
        gross_profit_change_pct=profit_change_pct,
        gross_margin_change_percentage_points=margin_change,
        explanation="",
        limitations=default_limitations(),
    )
    result.explanation = explain_deterministically(result)
    return result


def default_limitations() -> list[str]:
    return [
        "Sales volume changes proportionally scale both revenue and COGS.",
        "Selling-price changes affect revenue only; supplier-cost changes affect COGS only.",
        "The purchasing mix and unit volumes are otherwise held constant.",
        "This is deterministic sensitivity analysis, not a demand or profit forecast.",
        "Demand elasticity, taxes, discounts, fixed costs, inventory constraints, and currency conversion are not modeled.",
    ]


def explain_deterministically(result: ProfitSensitivityResult) -> str:
    """Render a concise explanation from calculated values without an LLM."""
    change_label = "increase" if result.gross_profit_change >= 0 else "decrease"
    text = (
        f"Under the supplied assumptions, gross profit would {change_label} by "
        f"{result.currency} {abs(result.gross_profit_change):,.2f}, from "
        f"{result.currency} {result.baseline_gross_profit:,.2f} to "
        f"{result.currency} {result.scenario_gross_profit:,.2f}."
    )
    if result.baseline_gross_profit == 0:
        text += " A percentage change is undefined because baseline gross profit is zero."
    elif result.gross_profit_change_pct is not None:
        text += f" That is a {result.gross_profit_change_pct:+}% change in gross profit."
    if result.gross_margin_change_percentage_points is not None:
        text += (
            f" Gross margin moves from {result.baseline_gross_margin_pct}% to "
            f"{result.scenario_gross_margin_pct}% "
            f"({result.gross_margin_change_percentage_points:+} percentage points)."
        )
    else:
        text += " Gross margin is undefined because scenario revenue is zero."
    return text
