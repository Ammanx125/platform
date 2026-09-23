# app/services/analytics/operations.py
"""
The fixed operation set for KPI formulas.

A formula is a JSON tree. Every node has an `op` field selecting the
operation. Operations recursively evaluate their arguments.

Shape reference:

    {"op": "sum", "concept": "Finance.Revenue"}
    {"op": "sum", "concept": "Procurement.PurchasePrice",
                  "group_by": ["Procurement.Supplier"]}
    {"op": "avg", "concept": "Inventory.StockLevel"}
    {"op": "count", "concept": "*"}
    {"op": "min", "concept": "Finance.Revenue"}
    {"op": "max", "concept": "Finance.Revenue"}

    {"op": "ratio",
     "numerator":   {"op": "sum", "concept": "Finance.Revenue"},
     "denominator": {"op": "sum", "concept": "Finance.Cost"},
     "scale": 100}

    {"op": "difference",
     "left":  {"op": "sum", "concept": "Finance.Revenue"},
     "right": {"op": "sum", "concept": "Finance.Cost"}}

    {"op": "pct_change",
     "from": {"op": "sum", "concept": "Finance.Revenue"},
     "to":   {"op": "sum", "concept": "Finance.Revenue"}}

Only ops listed here are recognized. Unknown ops raise KPIFormulaError.
Adding an op is a code change — there is no way to inject new behavior
via formula JSON alone. That's the security boundary.
"""
from __future__ import annotations

from typing import Any


class KPIFormulaError(Exception):
    """The formula tree is malformed, references an unknown op, or uses
    an op with invalid arguments."""


# Names of all supported ops, in a single place so the evaluator and the
# API can both reference them.
SUPPORTED_OPS: frozenset[str] = frozenset({
    "sum",
    "avg",
    "count",
    "min",
    "max",
    "ratio",
    "difference",
    "pct_change",
})


def get_op(name: str) -> str:
    """
    Validate an op name. Returns it if recognized; raises otherwise.
    Kept as a function so callers don't import SUPPORTED_OPS directly and
    so we have one place to hook future aliasing.
    """
    if name not in SUPPORTED_OPS:
        raise KPIFormulaError(
            f"unknown op {name!r}; supported ops: {sorted(SUPPORTED_OPS)}"
        )
    return name


def require_field(node: dict[str, Any], field: str, *, op: str) -> Any:
    if field not in node:
        raise KPIFormulaError(f"op {op!r} requires field {field!r}")
    return node[field]