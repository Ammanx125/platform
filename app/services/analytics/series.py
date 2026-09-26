# app/services/analytics/series.py
"""
Build time series from staged rows.

A series is a list of (timestamp, value) points, optionally grouped by an
entity (supplier, product, customer). The builder connects:

  - StagedRow.raw_data            (the values)
  - SemanticMapping               (concept -> raw column, per source)
  - RowTimestamp                  (the time basis)

Time basis fallback: each row picks the first available timestamp from the
detector's `time_basis_preference` list. If none of the preferred bases are
present, the row is skipped (with a counted reason).

Rows without a value or a timestamp are skipped, not errored. Detectors get
clean data; the caller can inspect skip counts if it cares.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.dataset import DataSource, StagedRow
from app.db.models.semantic import SemanticMapping
from app.db.models.timestamps import RowTimestamp
from app.services.analytics.context import _key_of, _to_float

DEFAULT_TIME_BASIS_PREFERENCE: tuple[str, ...] = (
    "content",
    "stream",
    "file",
    "ingestion",
)


@dataclass
class TimeSeriesPoint:
    timestamp: datetime
    value: float
    source_id: uuid.UUID


@dataclass
class TimeSeries:
    group_key: str            # "" for ungrouped
    group_label: str          # human-readable; falls back to group_key
    value_concept: str
    group_by_concept: str | None
    points: list[TimeSeriesPoint] = field(default_factory=list)
    time_basis_used: str = ""  # dominant basis across the points


@dataclass
class SeriesBuildResult:
    series: list[TimeSeries]
    rows_seen: int
    rows_used: int
    rows_skipped_no_value: int
    rows_skipped_no_timestamp: int
    rows_skipped_no_mapping: int


async def build_series(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_ids: list[uuid.UUID] | None,
    value_concept: str,
    group_by_concept: str | None,
    time_basis_preference: list[str] | None = None,
    row_limit: int = 100_000,
) -> SeriesBuildResult:
    """
    Load staged rows for the tenant (optionally scoped to sources), resolve
    the value and group-by concepts to raw columns, read timestamps from
    RowTimestamp, and produce grouped time series.
    """
    preference = list(time_basis_preference or DEFAULT_TIME_BASIS_PREFERENCE)
    # Validate preference values against known bases.
    from app.services.timestamps.constants import ALL_BASES
    bad = [b for b in preference if b not in ALL_BASES]
    if bad:
        raise ValueError(f"unknown time basis in preference: {bad}")

    # 1. Resolve sources.
    src_stmt = select(DataSource.id).where(
        DataSource.tenant_id == tenant_id,
        DataSource.is_active.is_(True),
    )
    if source_ids is not None:
        src_stmt = src_stmt.where(DataSource.id.in_(source_ids))
    resolved_sources = list((await db.execute(src_stmt)).scalars().all())
    if not resolved_sources:
        return SeriesBuildResult(
            series=[], rows_seen=0, rows_used=0,
            rows_skipped_no_value=0, rows_skipped_no_timestamp=0,
            rows_skipped_no_mapping=0,
        )

    # 2. Load confirmed mappings.
    mappings = (
        await db.execute(
            select(SemanticMapping).where(
                SemanticMapping.source_id.in_(resolved_sources),
                SemanticMapping.status == "confirmed",
                SemanticMapping.canonical_concept_key.in_(
                    [value_concept] + ([group_by_concept] if group_by_concept else [])
                ),
            )
        )
    ).scalars().all()

    # {source_id: {concept_key: column_name}}
    column_map: dict[uuid.UUID, dict[str, str]] = {}
    for m in mappings:
        column_map.setdefault(m.source_id, {})[m.canonical_concept_key] = m.source_column

    # 3. Load rows.
    rows = (
        await db.execute(
            select(StagedRow)
            .where(StagedRow.source_id.in_(resolved_sources))
            .limit(row_limit)
        )
    ).scalars().all()
    if not rows:
        return SeriesBuildResult(
            series=[], rows_seen=0, rows_used=0,
            rows_skipped_no_value=0, rows_skipped_no_timestamp=0,
            rows_skipped_no_mapping=0,
        )

    # 4. Load timestamps for those rows.
    row_ids = [r.id for r in rows]
    ts_rows = (
        await db.execute(
            select(RowTimestamp.staged_row_id, RowTimestamp.kind, RowTimestamp.timestamp)
            .where(RowTimestamp.staged_row_id.in_(row_ids))
        )
    ).all()
    # {staged_row_id: {kind: timestamp}}
    ts_by_row: dict[uuid.UUID, dict[str, datetime]] = {}
    for row_id, kind, ts in ts_rows:
        ts_by_row.setdefault(row_id, {})[kind] = ts

    # 5. Bucket into groups.
    by_group: dict[str, TimeSeries] = {}
    rows_seen = 0
    rows_used = 0
    skipped_no_value = 0
    skipped_no_ts = 0
    skipped_no_mapping = 0
    basis_counts: dict[str, int] = {}

    for row in rows:
        rows_seen += 1
        source_map = column_map.get(row.source_id)
        if source_map is None:
            skipped_no_mapping += 1
            continue

        value_col = source_map.get(value_concept)
        if value_col is None:
            skipped_no_mapping += 1
            continue

        raw_value = row.raw_data.get(value_col)
        value = _to_float(raw_value)
        if value is None:
            skipped_no_value += 1
            continue

        # Group key
        if group_by_concept:
            group_col = source_map.get(group_by_concept)
            if group_col is None:
                skipped_no_mapping += 1
                continue
            group_key = _key_of(row.raw_data.get(group_col))
            group_label = group_key
        else:
            group_key = ""
            group_label = ""

        # Timestamp
        row_ts = ts_by_row.get(row.id, {})
        chosen_kind: str | None = None
        chosen_ts: datetime | None = None
        for kind in preference:
            if kind in row_ts:
                chosen_kind = kind
                chosen_ts = row_ts[kind]
                break
        if chosen_ts is None:
            skipped_no_ts += 1
            continue
        assert chosen_kind is not None

        basis_counts[chosen_kind] = basis_counts.get(chosen_kind, 0) + 1

        if group_key not in by_group:
            by_group[group_key] = TimeSeries(
                group_key=group_key,
                group_label=group_label,
                value_concept=value_concept,
                group_by_concept=group_by_concept,
            )
        by_group[group_key].points.append(TimeSeriesPoint(
            timestamp=chosen_ts,
            value=value,
            source_id=row.source_id,
        ))
        rows_used += 1

    # 6. Sort each series by timestamp. Set the dominant basis.
    series = []
    for s in by_group.values():
        s.points.sort(key=lambda p: p.timestamp)
        # Dominant basis = most common kind across this series' points.
        # We only tracked the count globally; per-series would require
        # keeping kind per point. Simplify: use the first preference kind
        # that appears anywhere in basis_counts for the series. In practice
        # all points of a series will use the same basis unless data is mixed.
        s.time_basis_used = next(
            (k for k in preference if k in basis_counts), ""
        )
        series.append(s)

    return SeriesBuildResult(
        series=series,
        rows_seen=rows_seen,
        rows_used=rows_used,
        rows_skipped_no_value=skipped_no_value,
        rows_skipped_no_timestamp=skipped_no_ts,
        rows_skipped_no_mapping=skipped_no_mapping,
    )