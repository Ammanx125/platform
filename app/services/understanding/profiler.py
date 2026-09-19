# app/services/understanding/profiler.py
"""
Deterministic profiler.

Input: a list of raw row dicts (already parsed from StagedRow.raw_data).
Output: DataProfileResult.

No I/O, no DB, no LLM. Everything here is pure Python so it's fully testable.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from datetime import datetime
from typing import Any

from app.services.understanding.types import (
    CandidateKey,
    ColumnProfile,
    DataProfileResult,
)

# Cap how many distinct values we count exactly. Beyond this, we stop tracking
# distinct values to keep memory bounded on wide high-cardinality columns.
_MAX_TRACKED_DISTINCT = 5000

# How many top values to keep per column in the output.
_TOP_N = 10

# How many sample values to keep per column.
_SAMPLE_N = 5

# Date formats we try, in order. Cheap-first to avoid regex cost on every value.
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
)

_INT_RE = re.compile(r"^-?\d+$")
_FLOAT_RE = re.compile(r"^-?\d+\.\d+$")


def _try_int(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return True
    if isinstance(v, str) and _INT_RE.match(v.strip()):
        return True
    return False


def _try_float(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    if isinstance(v, str) and (_INT_RE.match(v.strip()) or _FLOAT_RE.match(v.strip())):
        return True
    return False


def _try_date(v: Any) -> bool:
    if isinstance(v, datetime):
        return True
    if not isinstance(v, str):
        return False
    s = v.strip()
    if not s:
        return False
    for fmt in _DATE_FORMATS:
        try:
            datetime.strptime(s, fmt)
            return True
        except ValueError:
            continue
    return False


def _try_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return True
    if isinstance(v, str) and v.strip().lower() in ("true", "false", "yes", "no"):
        return True
    return False


def _is_null(v: Any) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def _infer_column_type(values: list[Any]) -> str:
    non_null = [v for v in values if not _is_null(v)]
    if not non_null:
        return "empty"

    checks = (
        ("boolean", _try_bool),
        ("integer", _try_int),
        ("float", _try_float),
        ("date", _try_date),
    )

    # Count how many values pass each check. If a check passes for >=95% we
    # accept that type. Float is checked after integer so "42" isn't float.
    total = len(non_null)
    for type_name, fn in checks:
        passing = sum(1 for v in non_null if fn(v))
        if passing / total >= 0.95:
            return type_name

    if all(isinstance(v, str) for v in non_null):
        if not any(fn(v) for _, fn in checks for v in non_null):
            return "string"

    return "mixed"


def _to_number(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip()
        if _INT_RE.match(s) or _FLOAT_RE.match(s):
            try:
                return float(s)
            except ValueError:
                return None
    return None


def _column_profile(name: str, values: list[Any], row_count: int) -> ColumnProfile:
    null_count = sum(1 for v in values if _is_null(v))
    non_null = [v for v in values if not _is_null(v)]
    distinct_set = set()
    track_distinct = True
    for v in non_null:
        # Hashables only; stringify the unhashable ones.
        try:
            distinct_set.add(v)
        except TypeError:
            distinct_set.add(str(v))
        if len(distinct_set) > _MAX_TRACKED_DISTINCT:
            track_distinct = False
            break

    distinct_count = len(distinct_set) if track_distinct else -1
    distinct_pct = (distinct_count / len(non_null)) if (track_distinct and non_null) else 0.0

    inferred = _infer_column_type(values)

    profile = ColumnProfile(
        name=name,
        inferred_type=inferred,
        null_count=null_count,
        null_pct=(null_count / row_count) if row_count else 0.0,
        distinct_count=distinct_count,
        distinct_pct=distinct_pct,
        sample_values=non_null[:_SAMPLE_N],
    )

    if inferred in ("integer", "float"):
        nums = [n for n in (_to_number(v) for v in non_null) if n is not None]
        if nums:
            profile.min = min(nums)
            profile.max = max(nums)
            profile.mean = sum(nums) / len(nums)
            if len(nums) > 1:
                mean = profile.mean
                var = sum((x - mean) ** 2 for x in nums) / (len(nums) - 1)
                profile.std = math.sqrt(var)
            else:
                profile.std = 0.0

    elif inferred in ("string", "mixed", "date", "boolean"):
        # min/max for lexical comparison of stringified values (cheap, useful)
        str_vals = [str(v) for v in non_null]
        if str_vals:
            profile.min = min(str_vals)
            profile.max = max(str_vals)

    # Top values — bounded by Counter on a sample if distinct count is huge.
    if track_distinct:
        counter = Counter()
        for v in non_null:
            try:
                counter[v] += 1
            except TypeError:
                counter[str(v)] += 1
        profile.top_values = [
            {"value": v, "count": c} for v, c in counter.most_common(_TOP_N)
        ]

    return profile


def _find_candidate_keys(
    columns: list[str], rows: list[dict[str, Any]]
) -> list[CandidateKey]:
    """
    Single-column candidate keys: any column whose non-null distinct count
    equals the non-null row count. Multi-column keys are Step 6+ (too
    combinatorically expensive for a first pass on wide tables).
    """
    keys: list[CandidateKey] = []
    row_count = len(rows)

    for col in columns:
        values = [r.get(col) for r in rows]
        non_null = [v for v in values if not _is_null(v)]
        if not non_null:
            continue
        try:
            distinct = len({v for v in non_null})
        except TypeError:
            distinct = len({str(v) for v in non_null})
        if distinct == len(non_null) == row_count:
            keys.append(
                CandidateKey(columns=[col], uniqueness=1.0, distinct_count=distinct)
            )
    return keys


def _count_duplicate_rows(rows: list[dict[str, Any]]) -> int:
    seen: Counter = Counter()
    for r in rows:
        key = tuple(sorted((k, str(v)) for k, v in r.items()))
        seen[key] += 1
    return sum(c - 1 for c in seen.values() if c > 1)


def profile_rows(rows: list[dict[str, Any]]) -> DataProfileResult:
    """
    Main entry point. Takes parsed rows (list of dicts) and returns a
    DataProfileResult. Empty input returns an empty profile, not an error.
    """
    if not rows:
        return DataProfileResult(
            row_count=0,
            column_count=0,
            duplicate_row_count=0,
            columns=[],
            candidate_keys=[],
        )

    # Columns = union of keys across rows, preserving first-seen order.
    columns: list[str] = []
    seen: set[str] = set()
    for r in rows:
        for k in r.keys():
            if k not in seen:
                seen.add(k)
                columns.append(k)

    column_profiles = [
        _column_profile(col, [r.get(col) for r in rows], len(rows))
        for col in columns
    ]

    return DataProfileResult(
        row_count=len(rows),
        column_count=len(columns),
        duplicate_row_count=_count_duplicate_rows(rows),
        columns=column_profiles,
        candidate_keys=_find_candidate_keys(columns, rows),
    )