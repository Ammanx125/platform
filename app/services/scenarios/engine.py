# app/services/scenarios/engine.py
"""
The Scenario Lab calculation engine.

Pure functions only. This module must never touch the database, the
filesystem, the network, or an LLM. Same baseline + same assumptions
always produce the same result. Callers (baseline.py, service.py) are
responsible for loading and validating the data that goes in; the engine
is responsible for arithmetic that can be unit-tested by hand.

Scenario type: ``supplier_spend_sensitivity_v1``

Question answered:
    "If supplier prices rise by X%, how much would our purchase spend
     increase, and which suppliers contribute most to that increase?"

Model:
    projected_spend = baseline_spend * (1 + price_change_pct / 100)

Applied per supplier, then summed. Quantities and the purchasing mix are
held constant by construction. This is a *sensitivity* estimate, not a
forecast: it says what the existing purchasing pattern would cost at a
different price level, not what will actually be bought next period.

Money is Decimal. Percentages arrive as Decimal. No binary floats are
used anywhere in this module; rounding happens once, at the end, through
`quantize`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, localcontext

SCENARIO_TYPE = "supplier_spend_sensitivity_v1"
FORMULA_VERSION = 1

# Two decimal places: the engine is currency-agnostic, and Sansa's staged
# data carries money at (at most) this precision.
_MONEY_QUANT = Decimal("0.01")
_PCT_QUANT = Decimal("0.01")

# Demo bounds from the release decision. These are policy, not physics:
# they keep a demo from producing a 400% "what-if" that nobody asked for.
# The service layer applies them; the engine only enforces what makes the
# arithmetic undefined.
SUPPLIER_PRICE_CHANGE_MIN = Decimal("-50")
SUPPLIER_PRICE_CHANGE_MAX = Decimal("100")


class ScenarioCalculationError(Exception):
    """Inputs are individually well-formed but the calculation is undefined."""


@dataclass(frozen=True)
class SupplierSpend:
    """One supplier's baseline spend. `supplier_key` is a stable label."""
    supplier_key: str
    baseline_spend: Decimal


@dataclass(frozen=True)
class SupplierProjection:
    supplier_key: str
    baseline_spend: Decimal
    projected_spend: Decimal
    increase: Decimal
    share_of_total_increase_pct: Decimal | None


@dataclass
class SpendSensitivityResult:
    scenario_type: str
    formula_version: int
    price_change_pct: Decimal
    baseline_spend: Decimal
    projected_spend: Decimal
    increase: Decimal
    increase_pct: Decimal | None
    suppliers: list[SupplierProjection] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


def _round_money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP)


def _round_pct(value: Decimal) -> Decimal:
    return value.quantize(_PCT_QUANT, rounding=ROUND_HALF_UP)


def _require_finite(value: Decimal, field_name: str) -> None:
    if not value.is_finite():
        raise ScenarioCalculationError(f"{field_name} must be a finite number")


def simulate_supplier_spend(
    *,
    suppliers: list[SupplierSpend],
    price_change_pct: Decimal,
) -> SpendSensitivityResult:
    """
    Project purchase spend under a uniform supplier price change.

    Raises ScenarioCalculationError when the answer is undefined:
      - non-finite or negative baseline spend
      - non-finite price change, or a price change that would make the
        price level zero or negative (i.e. <= -100%)
      - no suppliers at all

    An empty-but-valid baseline (everything zero) is allowed and yields
    zero spend with a ``None`` percentage: 0 -> 0 has no meaningful rate.
    """
    _require_finite(price_change_pct, "price_change_pct")
    if price_change_pct <= Decimal("-100"):
        raise ScenarioCalculationError(
            "price_change_pct must be greater than -100; a price level of "
            "zero or below is not a meaningful scenario"
        )
    if not suppliers:
        raise ScenarioCalculationError("baseline has no supplier spend to simulate")

    # A wider precision context than the default 28 digits. Large ledgers
    # with high-precision unit prices can exhaust the default when
    # multiplied and summed; 40 keeps the intermediate totals exact.
    with localcontext() as ctx:
        ctx.prec = 40
        multiplier = Decimal(1) + (price_change_pct / Decimal(100))

        rows: list[SupplierProjection] = []
        baseline_total = Decimal(0)
        projected_total = Decimal(0)
        for entry in suppliers:
            _require_finite(entry.baseline_spend, "baseline_spend")
            if entry.baseline_spend < 0:
                raise ScenarioCalculationError(
                    f"negative baseline spend for supplier "
                    f"{entry.supplier_key!r}; spend must be non-negative"
                )
            projected = entry.baseline_spend * multiplier
            baseline_total += entry.baseline_spend
            projected_total += projected
            rows.append(
                SupplierProjection(
                    supplier_key=entry.supplier_key,
                    baseline_spend=entry.baseline_spend,
                    projected_spend=projected,
                    increase=projected - entry.baseline_spend,
                    share_of_total_increase_pct=None,
                )
            )

        total_increase = projected_total - baseline_total
        increase_pct: Decimal | None
        if baseline_total > 0:
            increase_pct = _round_pct(
                total_increase / baseline_total * Decimal(100)
            )
        else:
            increase_pct = None

        # Contribution only makes sense when something actually increases.
        # A zero or negative total increase (price cut) has no ranking.
        ranked: list[SupplierProjection] = []
        for row in rows:
            share: Decimal | None = None
            if total_increase > 0:
                share = _round_pct(row.increase / total_increase * Decimal(100))
            ranked.append(
                SupplierProjection(
                    supplier_key=row.supplier_key,
                    baseline_spend=_round_money(row.baseline_spend),
                    projected_spend=_round_money(row.projected_spend),
                    increase=_round_money(row.increase),
                    share_of_total_increase_pct=share,
                )
            )
        ranked.sort(
            key=lambda r: (r.increase, r.baseline_spend, r.supplier_key),
            reverse=True,
        )

        return SpendSensitivityResult(
            scenario_type=SCENARIO_TYPE,
            formula_version=FORMULA_VERSION,
            price_change_pct=_round_pct(price_change_pct),
            baseline_spend=_round_money(baseline_total),
            projected_spend=_round_money(projected_total),
            increase=_round_money(total_increase),
            increase_pct=increase_pct,
            suppliers=ranked,
            limitations=default_limitations(),
        )


def default_limitations() -> list[str]:
    """
    The assumptions a reader must know to interpret the number honestly.
    Kept in the result (not just the UI) so saved scenarios stay auditable.
    """
    return [
        "Purchase quantities and the supplier mix are held constant.",
        "This is a sensitivity estimate, not a forecast of future spend.",
        "Only suppliers with confirmed spend data are included.",
        "Currency, taxes, discounts, and freight are not modeled separately.",
    ]


def _fmt_money(value: Decimal) -> str:
    """Group thousands for the template sentence. Display only, no rounding."""
    return f"{value:,.2f}"


def explain_deterministically(result: SpendSensitivityResult) -> str:
    """
    A plain-language summary that is correct by construction.

    The LLM explanation is optional and must never change the numbers;
    this template is the fallback when no provider is configured, and it
    is the only explanation shown for saved scenarios in v1.
    """
    if result.increase_pct is None:
        return (
            "The baseline spend is zero, so a percentage impact is undefined."
        )
    direction = "increase" if result.increase >= 0 else "decrease"
    head = (
        f"A {result.price_change_pct}% supplier price change would "
        f"{direction} modeled purchase spend from "
        f"{_fmt_money(result.baseline_spend)} to "
        f"{_fmt_money(result.projected_spend)} "
        f"({result.increase_pct:+}%)."
    )
    if not result.suppliers or result.increase <= 0:
        return head
    top = result.suppliers[0]
    if len(result.suppliers) == 1:
        return (
            f"{head} {top.supplier_key} accounts for all of the change, "
            f"because it is the only supplier in the baseline."
        )
    second = result.suppliers[1]
    return (
        f"{head} {top.supplier_key} contributes the most "
        f"({_fmt_money(top.increase)}, {top.share_of_total_increase_pct}% of the "
        f"increase), followed by {second.supplier_key} "
        f"({_fmt_money(second.increase)}, {second.share_of_total_increase_pct}%)."
    )
