# app/services/understanding/types.py
"""
Dataclasses shared between the profiler, quality rules, and schema inference.

These are deliberately pure: no SQLAlchemy, no FastAPI. The profiler produces
them; the service layer persists them; tests construct them directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ColumnProfile:
    name: str
    inferred_type: str          # integer|float|string|date|boolean|mixed|empty
    null_count: int
    null_pct: float
    distinct_count: int
    distinct_pct: float
    min: Any | None = None
    max: Any | None = None
    mean: float | None = None
    std: float | None = None
    top_values: list[dict[str, Any]] = field(default_factory=list)
    sample_values: list[Any] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "inferred_type": self.inferred_type,
            "null_count": self.null_count,
            "null_pct": self.null_pct,
            "distinct_count": self.distinct_count,
            "distinct_pct": self.distinct_pct,
            "min": self.min,
            "max": self.max,
            "mean": self.mean,
            "std": self.std,
            "top_values": self.top_values,
            "sample_values": self.sample_values,
        }


@dataclass
class CandidateKey:
    columns: list[str]
    uniqueness: float           # 1.0 = fully unique, 0.0 = all dupes
    distinct_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "columns": self.columns,
            "uniqueness": self.uniqueness,
            "distinct_count": self.distinct_count,
        }


@dataclass
class DataProfileResult:
    row_count: int
    column_count: int
    duplicate_row_count: int
    columns: list[ColumnProfile]
    candidate_keys: list[CandidateKey]

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_count": self.row_count,
            "column_count": self.column_count,
            "duplicate_row_count": self.duplicate_row_count,
            "columns": [c.to_dict() for c in self.columns],
            "candidate_keys": [k.to_dict() for k in self.candidate_keys],
        }


@dataclass
class QualityIssue:
    code: str
    severity: str               # info|warning|error
    column: str | None
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "column": self.column,
            "detail": self.detail,
        }