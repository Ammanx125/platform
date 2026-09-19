# app/services/understanding/quality.py
"""
Rule-based quality checks over a DataProfileResult.

No I/O, no DB, no LLM. Each rule is a small function that takes the profile
plus (optionally) the raw rows and returns zero or more QualityIssue items.
"""
from __future__ import annotations

import re
from typing import Any

from app.services.understanding.types import (
    DataProfileResult,
    QualityIssue,
)

# Column-name heuristics for the quantity rule. Matched case-insensitively as
# a substring, so "order_quantity" and "qty_ordered" both trip.
_QUANTITY_HINTS = ("quantity", "qty", "count", "units", "stock")

# Thresholds
_MISSINGNESS_WARNING = 0.20
_MISSINGNESS_ERROR = 0.50
_INVALID_DATE_WARNING = 0.05      # >5% unparseable in a column inferred as date
_INCONSISTENT_IDENTIFIER_MIN = 2  # at least 2 casing variants to flag

_WHITESPACE_RE = re.compile(r"\s+")


def _norm_key(s: str) -> str:
    return _WHITESPACE_RE.sub(" ", s.strip().lower())


def _check_missingness(profile: DataProfileResult) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    for col in profile.columns:
        if col.null_pct >= _MISSINGNESS_ERROR:
            issues.append(QualityIssue(
                code="high_missingness",
                severity="error",
                column=col.name,
                detail=f"{col.null_pct:.0%} of values are null",
            ))
        elif col.null_pct >= _MISSINGNESS_WARNING:
            issues.append(QualityIssue(
                code="high_missingness",
                severity="warning",
                column=col.name,
                detail=f"{col.null_pct:.0%} of values are null",
            ))
    return issues


def _check_mixed_types(profile: DataProfileResult) -> list[QualityIssue]:
    return [
        QualityIssue(
            code="mixed_types",
            severity="warning",
            column=col.name,
            detail="column contains values that do not fit a single type",
        )
        for col in profile.columns
        if col.inferred_type == "mixed"
    ]


def _check_impossible_quantity(
    profile: DataProfileResult, rows: list[dict[str, Any]]
) -> list[QualityIssue]:
    """
    Flag quantity-like columns that contain negative values.
    Quantity is a heuristic on the column name; we don't try to be clever.
    """
    issues: list[QualityIssue] = []
    for col in profile.columns:
        lname = col.name.lower()
        if not any(hint in lname for hint in _QUANTITY_HINTS):
            continue
        negatives = 0
        for r in rows:
            v = r.get(col.name)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v < 0:
                negatives += 1
            elif isinstance(v, str):
                s = v.strip()
                if s.startswith("-"):
                    try:
                        if float(s) < 0:
                            negatives += 1
                    except ValueError:
                        pass
        if negatives:
            issues.append(QualityIssue(
                code="impossible_quantity",
                severity="warning",
                column=col.name,
                detail=f"{negatives} row(s) contain a negative value in a quantity column",
            ))
    return issues


def _check_invalid_dates(
    profile: DataProfileResult, rows: list[dict[str, Any]]
) -> list[QualityIssue]:
    """
    If a column inferred as 'date', count the values that don't parse. We
    accept the 95% inference threshold; this rule surfaces the remaining 5%.
    Uses the same parse logic as the profiler (kept simple, no imports).
    """
    from datetime import datetime

    formats = (
        "%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y",
        "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f",
    )

    def parses(v: Any) -> bool:
        if isinstance(v, datetime):
            return True
        if not isinstance(v, str):
            return False
        s = v.strip()
        if not s:
            return False
        for fmt in formats:
            try:
                datetime.strptime(s, fmt)
                return True
            except ValueError:
                continue
        return False

    issues: list[QualityIssue] = []
    for col in profile.columns:
        if col.inferred_type != "date":
            continue
        non_null = [r.get(col.name) for r in rows]
        non_null = [v for v in non_null if v is not None and v != ""]
        if not non_null:
            continue
        bad = sum(1 for v in non_null if not parses(v))
        ratio = bad / len(non_null)
        if ratio > _INVALID_DATE_WARNING:
            issues.append(QualityIssue(
                code="invalid_date",
                severity="warning",
                column=col.name,
                detail=f"{ratio:.0%} of non-null values do not parse as dates",
            ))
    return issues


def _check_inconsistent_identifiers(
    profile: DataProfileResult, rows: list[dict[str, Any]]
) -> list[QualityIssue]:
    """
    For each string column, look for distinct values whose *normalized* form
    is the same. E.g. "ACME" and "acme" and " Acme " all normalize to "acme"
    but are stored as three distinct values. Signals casing/whitespace drift.
    """
    issues: list[QualityIssue] = []
    for col in profile.columns:
        if col.inferred_type not in ("string", "mixed"):
            continue
        variants: dict[str, set[str]] = {}
        for r in rows:
            v = r.get(col.name)
            if not isinstance(v, str):
                continue
            s = v.strip()
            if not s:
                continue
            k = _norm_key(s)
            variants.setdefault(k, set()).add(s)
        offenders = sum(
            1 for variants_set in variants.values()
            if len(variants_set) >= _INCONSISTENT_IDENTIFIER_MIN
        )
        if offenders:
            issues.append(QualityIssue(
                code="inconsistent_identifier",
                severity="info",
                column=col.name,
                detail=f"{offenders} value(s) appear with multiple casing/whitespace variants",
            ))
    return issues


def _check_duplicate_entities(profile: DataProfileResult) -> list[QualityIssue]:
    """
    A candidate key that is unique at 100% isn't a problem. But if a column
    *looks* like a key (name ends in '_id' or is 'id') and is NOT unique,
    flag it.
    """
    issues: list[QualityIssue] = []
    unique_cols = {k.columns[0] for k in profile.candidate_keys if len(k.columns) == 1}
    for col in profile.columns:
        name = col.name.lower()
        looks_like_key = name == "id" or name.endswith("_id") or name in ("sku", "code")
        if looks_like_key and col.distinct_count > 0 and col.name not in unique_cols:
            issues.append(QualityIssue(
                code="duplicate_entity",
                severity="warning",
                column=col.name,
                detail="column looks like an identifier but is not unique",
            ))
    return issues


def assess_quality(
    profile: DataProfileResult, rows: list[dict[str, Any]]
) -> list[QualityIssue]:
    """
    Run every rule and return the combined issue list.
    """
    issues: list[QualityIssue] = []
    issues.extend(_check_missingness(profile))
    issues.extend(_check_mixed_types(profile))
    issues.extend(_check_impossible_quantity(profile, rows))
    issues.extend(_check_invalid_dates(profile, rows))
    issues.extend(_check_inconsistent_identifiers(profile, rows))
    issues.extend(_check_duplicate_entities(profile))
    return issues