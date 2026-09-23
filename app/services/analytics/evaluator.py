# app/services/analytics/evaluator.py
"""
Formula tree evaluator.

Walks a JSON formula tree and produces a numeric result (or dict of
results, when grouped). Every operation dispatches to a small function in
this module. No `eval`, no dynamic imports, no user-provided code.

Grouping semantics: when an op has `group_by`, the concept's values are
bucketed by group key first, then the op is applied per bucket. The result
is a dict {group_key: value}. Nested ops may or may not support grouping;
see each op's implementation.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.services.analytics.context import EvaluationContext
from app.services.analytics.operations import (
    KPIFormulaError,
    get_op,
    require_field,
)


def _require_number(value: Any, *, op: str, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise KPIFormulaError(
            f"op {op!r}: {field_name} must evaluate to a number, got {type(value).__name__}"
        )
    return float(value)


def _require_dict_of_numbers(value: Any, *, op: str, field_name: str) -> dict[str, float]:
    if not isinstance(value, dict):
        raise KPIFormulaError(
            f"op {op!r}: {field_name} must evaluate to a grouped object, got {type(value).__name__}"
        )
    out: dict[str, float] = {}
    for k, v in value.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise KPIFormulaError(
                f"op {op!r}: group {k!r} of {field_name} is not a number"
            )
        out[str(k)] = float(v)
    return out


# ---------- per-op implementations ----------
#
# Each takes (node, context) and returns float | dict[str, float].
# Raising KPIFormulaError on malformed input is required.

def _op_sum(node: dict[str, Any], ctx: EvaluationContext) -> float | dict[str, float]:
    concept = require_field(node, "concept", op="sum")
    group_by = node.get("group_by") or None
    cv = ctx.values_for(concept, group_by=group_by)
    if group_by:
        buckets: dict[str, float] = defaultdict(float)
        for k, v in zip(cv.keys, cv.values, strict=True):
            buckets[k] += v
        return dict(buckets)
    return float(sum(cv.values))


def _op_avg(node: dict[str, Any], ctx: EvaluationContext) -> float | dict[str, float]:
    concept = require_field(node, "concept", op="avg")
    group_by = node.get("group_by") or None
    cv = ctx.values_for(concept, group_by=group_by)
    if group_by:
        buckets: dict[str, list[float]] = defaultdict(list)
        for k, v in zip(cv.keys, cv.values, strict=True):
            buckets[k].append(v)
        return {k: sum(vs) / len(vs) for k, vs in buckets.items() if vs}
    if not cv.values:
        return 0.0
    return float(sum(cv.values) / len(cv.values))


def _op_count(node: dict[str, Any], ctx: EvaluationContext) -> float | dict[str, float]:
    concept = require_field(node, "concept", op="count")
    group_by = node.get("group_by") or None
    # `count` with concept `*` counts rows regardless of numeric coercibility.
    if concept == "*":
        if group_by:
            raise KPIFormulaError("count with concept='*' does not support group_by")
        return float(len(ctx._rows))  # noqa: SLF001 — intentional intra-package access
    cv = ctx.values_for(concept, group_by=group_by)
    if group_by:
        buckets: dict[str, int] = defaultdict(int)
        for k in cv.keys:
            buckets[k] += 1
        return {k: float(v) for k, v in buckets.items()}
    return float(len(cv.values))


def _op_min(node: dict[str, Any], ctx: EvaluationContext) -> float:
    concept = require_field(node, "concept", op="min")
    cv = ctx.values_for(concept)
    if not cv.values:
        return 0.0
    return float(min(cv.values))


def _op_max(node: dict[str, Any], ctx: EvaluationContext) -> float:
    concept = require_field(node, "concept", op="max")
    cv = ctx.values_for(concept)
    if not cv.values:
        return 0.0
    return float(max(cv.values))


def _op_ratio(node: dict[str, Any], ctx: EvaluationContext) -> float | dict[str, float]:
    num_node = require_field(node, "numerator", op="ratio")
    den_node = require_field(node, "denominator", op="ratio")
    scale = float(node.get("scale", 1))

    num = evaluate(num_node, ctx)
    den = evaluate(den_node, ctx)

    # Grouped vs ungrouped must match on both sides.
    num_is_dict = isinstance(num, dict)
    den_is_dict = isinstance(den, dict)
    if num_is_dict != den_is_dict:
        raise KPIFormulaError(
            "op 'ratio': numerator and denominator must be both grouped or both ungrouped"
        )

    if num_is_dict:
        num_d = _require_dict_of_numbers(num, op="ratio", field_name="numerator")
        den_d = _require_dict_of_numbers(den, op="ratio", field_name="denominator")
        out: dict[str, float] = {}
        for k, n in num_d.items():
            d = den_d.get(k, 0.0)
            out[k] = (n / d) * scale if d else 0.0
        return out

    n_f = _require_number(num, op="ratio", field_name="numerator")
    d_f = _require_number(den, op="ratio", field_name="denominator")
    return (n_f / d_f) * scale if d_f else 0.0


def _op_difference(node: dict[str, Any], ctx: EvaluationContext) -> float | dict[str, float]:
    left = evaluate(require_field(node, "left", op="difference"), ctx)
    right = evaluate(require_field(node, "right", op="difference"), ctx)

    left_is_dict = isinstance(left, dict)
    right_is_dict = isinstance(right, dict)
    if left_is_dict != right_is_dict:
        raise KPIFormulaError(
            "op 'difference': left and right must be both grouped or both ungrouped"
        )

    if left_is_dict:
        left_d = _require_dict_of_numbers(left, op="difference", field_name="left")
        right_d = _require_dict_of_numbers(right, op="difference", field_name="right")
        keys = set(left_d) | set(right_d)
        return {k: left_d.get(k, 0.0) - right_d.get(k, 0.0) for k in keys}

    return (
        _require_number(left, op="difference", field_name="left")
        - _require_number(right, op="difference", field_name="right")
    )


def _op_pct_change(node: dict[str, Any], ctx: EvaluationContext) -> float | dict[str, float]:
    frm = evaluate(require_field(node, "from", op="pct_change"), ctx)
    to = evaluate(require_field(node, "to", op="pct_change"), ctx)

    frm_is_dict = isinstance(frm, dict)
    to_is_dict = isinstance(to, dict)
    if frm_is_dict != to_is_dict:
        raise KPIFormulaError(
            "op 'pct_change': from and to must be both grouped or both ungrouped"
        )

    if frm_is_dict:
        frm_d = _require_dict_of_numbers(frm, op="pct_change", field_name="from")
        to_d = _require_dict_of_numbers(to, op="pct_change", field_name="to")
        keys = set(frm_d) | set(to_d)
        out: dict[str, float] = {}
        for k in keys:
            base = frm_d.get(k, 0.0)
            out[k] = ((to_d.get(k, 0.0) - base) / base) * 100 if base else 0.0
        return out

    f_f = _require_number(frm, op="pct_change", field_name="from")
    t_f = _require_number(to, op="pct_change", field_name="to")
    return ((t_f - f_f) / f_f) * 100 if f_f else 0.0


_DISPATCH = {
    "sum": _op_sum,
    "avg": _op_avg,
    "count": _op_count,
    "min": _op_min,
    "max": _op_max,
    "ratio": _op_ratio,
    "difference": _op_difference,
    "pct_change": _op_pct_change,
}


def evaluate(node: Any, ctx: EvaluationContext) -> float | dict[str, float]:
    """
    Evaluate a formula node. Returns a float for ungrouped formulas, a dict
    of {group_key: float} for grouped ones.

    Structural errors (missing `op`, unknown op, missing required field)
    raise KPIFormulaError. Numeric edge cases (empty values, division by
    zero) return 0.0 rather than raising — a KPI returning 0 for "no data"
    is more useful than a KPI crashing.
    """
    if not isinstance(node, dict):
        raise KPIFormulaError(f"formula node must be an object, got {type(node).__name__}")
    op_name = node.get("op")
    if not isinstance(op_name, str):
        raise KPIFormulaError("formula node must have a string 'op' field")
    op_name = get_op(op_name)
    return _DISPATCH[op_name](node, ctx)