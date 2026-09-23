# tests/unit/analytics/test_evaluator.py
import pytest

from app.services.analytics.context import EvaluationContext
from app.services.analytics.evaluator import evaluate
from app.services.analytics.operations import KPIFormulaError


def _ctx(rows: list[dict], mapping: dict[str, str]) -> EvaluationContext:
    """
    Build a context directly for unit tests. `mapping` maps concept_key ->
    column name for one synthetic source.
    """
    import uuid

    source_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    for r in rows:
        r["__source_id"] = str(source_id)
    return EvaluationContext(
        rows=rows,
        column_map={source_id: mapping},
        source_ids=[source_id],
    )


def test_sum_ungrouped():
    ctx = _ctx(
        [{"rev": "10"}, {"rev": "20"}, {"rev": "30"}],
        {"Finance.Revenue": "rev"},
    )
    assert evaluate({"op": "sum", "concept": "Finance.Revenue"}, ctx) == 60.0


def test_sum_grouped():
    ctx = _ctx(
        [
            {"sup": "A", "price": "10"},
            {"sup": "B", "price": "20"},
            {"sup": "A", "price": "30"},
        ],
        {"Procurement.PurchasePrice": "price", "Procurement.Supplier": "sup"},
    )
    result = evaluate(
        {
            "op": "sum",
            "concept": "Procurement.PurchasePrice",
            "group_by": ["Procurement.Supplier"],
        },
        ctx,
    )
    assert result == {"A": 40.0, "B": 20.0}


def test_avg():
    ctx = _ctx(
        [{"x": "2"}, {"x": "4"}, {"x": "6"}],
        {"Common.Quantity": "x"},
    )
    assert evaluate({"op": "avg", "concept": "Common.Quantity"}, ctx) == 4.0


def test_count():
    ctx = _ctx(
        [{"x": "2"}, {"x": "bad"}, {"x": "6"}],
        {"Common.Quantity": "x"},
    )
    # count of coercible values
    assert evaluate({"op": "count", "concept": "Common.Quantity"}, ctx) == 2.0


def test_count_star():
    ctx = _ctx(
        [{"x": "2"}, {"x": "bad"}, {"x": "6"}],
        {"Common.Quantity": "x"},
    )
    assert evaluate({"op": "count", "concept": "*"}, ctx) == 3.0


def test_min_max():
    ctx = _ctx([{"x": "5"}, {"x": "1"}, {"x": "9"}], {"Common.Quantity": "x"})
    assert evaluate({"op": "min", "concept": "Common.Quantity"}, ctx) == 1.0
    assert evaluate({"op": "max", "concept": "Common.Quantity"}, ctx) == 9.0


def test_ratio():
    ctx = _ctx(
        [{"rev": "100", "cost": "40"}],
        {"Finance.Revenue": "rev", "Finance.Cost": "cost"},
    )
    result = evaluate(
        {
            "op": "ratio",
            "numerator": {"op": "sum", "concept": "Finance.Revenue"},
            "denominator": {"op": "sum", "concept": "Finance.Cost"},
            "scale": 1,
        },
        ctx,
    )
    assert result == pytest.approx(2.5)


def test_ratio_zero_denominator_returns_zero():
    ctx = _ctx([{"rev": "100", "cost": "0"}], {"Finance.Revenue": "rev", "Finance.Cost": "cost"})
    result = evaluate(
        {
            "op": "ratio",
            "numerator": {"op": "sum", "concept": "Finance.Revenue"},
            "denominator": {"op": "sum", "concept": "Finance.Cost"},
        },
        ctx,
    )
    assert result == 0.0


def test_difference():
    ctx = _ctx(
        [{"rev": "100", "cost": "40"}],
        {"Finance.Revenue": "rev", "Finance.Cost": "cost"},
    )
    result = evaluate(
        {
            "op": "difference",
            "left": {"op": "sum", "concept": "Finance.Revenue"},
            "right": {"op": "sum", "concept": "Finance.Cost"},
        },
        ctx,
    )
    assert result == 60.0


def test_pct_change():
    ctx = _ctx(
        [{"old": "50", "new": "75"}],
        {"Common.Amount": "old", "Finance.Revenue": "new"},
    )
    result = evaluate(
        {
            "op": "pct_change",
            "from": {"op": "sum", "concept": "Common.Amount"},
            "to": {"op": "sum", "concept": "Finance.Revenue"},
        },
        ctx,
    )
    assert result == 50.0


def test_unknown_op_raises():
    ctx = _ctx([{"x": "1"}], {"Common.Quantity": "x"})
    with pytest.raises(KPIFormulaError, match="unknown op"):
        evaluate({"op": "magic", "concept": "Common.Quantity"}, ctx)


def test_missing_op_raises():
    ctx = _ctx([{"x": "1"}], {"Common.Quantity": "x"})
    with pytest.raises(KPIFormulaError, match="must have a string 'op'"):
        evaluate({"concept": "Common.Quantity"}, ctx)


def test_missing_field_raises():
    ctx = _ctx([{"x": "1"}], {"Common.Quantity": "x"})
    with pytest.raises(KPIFormulaError, match="requires field 'concept'"):
        evaluate({"op": "sum"}, ctx)


def test_currency_symbols_stripped():
    ctx = _ctx(
        [{"price": "$1,234.56"}, {"price": "€50"}],
        {"Procurement.PurchasePrice": "price"},
    )
    # The euro symbol is stripped, then "50" parses.
    result = evaluate({"op": "sum", "concept": "Procurement.PurchasePrice"}, ctx)
    assert result == pytest.approx(1284.56)