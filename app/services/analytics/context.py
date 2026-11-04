# app/services/analytics/context.py
"""
Evaluation context: resolves concepts to actual numeric values from
StagedRow.raw_data.

This is the bridge between the semantic layer (concepts) and the physical
layer (staged rows with arbitrary column names). Given a tenant, a set of
sources, and a concept key, it produces the list of numeric values that
the operations will aggregate over.

Design decisions:
  - We read StagedRow.raw_data for each source and use SemanticMapping to
    find which raw column name corresponds to the requested concept.
  - Concept-to-column resolution is per-source: two different sources may
    name the same concept differently (one calls it `supplier`, another
    `vendor_name`).
  - Type coercion is lenient: values that can't be coerced to float are
    skipped and counted. We don't fail a KPI because 3 rows out of 10,000
    have a typo.
  - Grouping values (for group_by) are NOT coerced to numbers. They're
    used as dict keys, stringified.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.dataset import StagedRow
from app.db.models.semantic import CanonicalConcept, SemanticMapping
from app.services.analytics.periods import Period


def _to_float(value: Any) -> float | None:
    """Coerce a raw value to float. Returns None if uncoercible."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # Strip common currency symbols and thousands separators.
        s = s.replace(",", "").replace("$", "").replace("€", "").replace("£", "")
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _key_of(value: Any) -> str:
    """Grouping keys are stringified for stability across JSON types."""
    if value is None:
        return ""
    return str(value)


@dataclass(frozen=True)
class ConceptMetadata:
    """Catalog metadata for a concept, independent of any KPI definition."""
    key: str
    display_name: str
    description: str | None
    unit: str | None
    currency: str | None


@dataclass
class ConceptColumn:
    """A resolved concept-to-column binding for one source."""
    source_id: uuid.UUID
    concept_key: str
    column_name: str


@dataclass
class ConceptValues:
    """
    The result of resolving a concept across sources.

    values:   numeric values successfully coerced
    keys:     grouping keys aligned index-wise with `values`. Empty when
              the concept was requested without grouping.
    skipped:  count of raw values that failed coercion or were missing
    sources:  the source_ids that contributed at least one value
    """
    values: list[float] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)
    skipped: int = 0
    sources: list[uuid.UUID] = field(default_factory=list)


class EvaluationContext:
    """
    Holds the loaded rows for the requested sources, plus the mapping
    between concepts and columns. Built once per KPI evaluation.

    Usage:
        ctx = await EvaluationContext.load(db, tenant_id=..., source_ids=[...])
        values = ctx.values_for("Finance.Revenue")
        grouped = ctx.values_for("Procurement.PurchasePrice",
                                 group_by=["Procurement.Supplier"])
    """

    def __init__(
        self,
        *,
        rows: list[dict[str, Any]],
        column_map: dict[uuid.UUID, dict[str, str]],
        source_ids: list[uuid.UUID],
        concepts: dict[str, ConceptMetadata] | None = None,
    ) -> None:
        """
        rows:       the union of StagedRow.raw_data across all sources, plus
                    a synthetic `__source_id` key on each row.
        column_map: {source_id: {concept_key: column_name}}
        source_ids: the sources included in this context, in a stable order.
        concepts:   {concept_key: ConceptMetadata} for every concept mapped
                    in these sources. Empty when no concepts were loaded.
        """
        self._rows = rows
        self._column_map = column_map
        self._source_ids = source_ids
        self._concepts = concepts or {}

    # The resolved window, attached by load() so callers can report it.
    period: Period | None = None

    @classmethod
    async def load(
        cls,
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        source_ids: list[uuid.UUID] | None,
        row_limit: int = 100_000,
        period: str | None = None,
        period_count: int = 1,
    ) -> EvaluationContext:
        """
        Load rows + mappings for the given tenant and sources.

        If source_ids is None, uses all of the tenant's sources. Row load
        is capped at `row_limit` to prevent unbounded memory; callers that
        need more should implement aggregation-in-SQL (a future Step 8x
        concern, not 8a).

        `period` restricts the rows to a reporting window anchored on the
        tenant's newest data point (see app/services/analytics/periods.py).
        Rows without a row timestamp are excluded from a bounded window:
        their date is unknown, so claiming they fall inside the period
        would be a guess.
        """
        from app.db.models.dataset import DataSource

        # Resolve which sources to include.
        src_stmt = select(DataSource.id).where(
            DataSource.tenant_id == tenant_id,
            DataSource.is_active.is_(True),
        )
        if source_ids is not None:
            src_stmt = src_stmt.where(DataSource.id.in_(source_ids))
        resolved_sources = list((await db.execute(src_stmt)).scalars().all())
        if not resolved_sources:
            return cls(rows=[], column_map={}, source_ids=[])

        # Load confirmed mappings for these sources.
        map_rows = (
            await db.execute(
                select(SemanticMapping).where(
                    SemanticMapping.source_id.in_(resolved_sources),
                    SemanticMapping.status == "confirmed",
                )
            )
        ).scalars().all()

        column_map: dict[uuid.UUID, dict[str, str]] = {}
        for m in map_rows:
            column_map.setdefault(m.source_id, {})[m.canonical_concept_key] = m.source_column

        # Load the concepts behind those mappings once, so callers can
        # label a value ("Revenue (ETB)") without a second query.
        concept_keys = {m.canonical_concept_key for m in map_rows}
        concepts: dict[str, ConceptMetadata] = {}
        if concept_keys:
            concept_rows = (
                await db.execute(
                    select(CanonicalConcept).where(
                        CanonicalConcept.key.in_(concept_keys)
                    )
                )
            ).scalars().all()
            concepts = {
                c.key: ConceptMetadata(
                    key=c.key,
                    display_name=c.display_name,
                    description=c.description,
                    unit=c.unit,
                    currency=c.currency,
                )
                for c in concept_rows
            }

        # Load staged rows. `__source_id` is injected so operations can
        # attribute values back to a source when needed.
        from app.services.datasets.scope import (
            active_snapshot_filter,
            deleted_snapshot_filter,
        )

        scope_clauses = await active_snapshot_filter(
            db, tenant_id=tenant_id, source_ids=resolved_sources,
        )
        # Deleted source files contribute nothing, whatever the mode.
        scope_clauses = scope_clauses + await deleted_snapshot_filter(
            db, tenant_id=tenant_id
        )

        rows_stmt = (
            select(StagedRow)
            .where(StagedRow.source_id.in_(resolved_sources))
            .where(*scope_clauses)
            .limit(row_limit)
        )

        # Reporting window. Resolved against the newest timestamp in scope,
        # not against now(), so a dataset covering last quarter is evaluated
        # as "last month of that data" rather than as an empty month.
        resolved_period = None
        if period and period != "all_time":
            from app.db.models.timestamps import RowTimestamp
            from app.services.analytics.periods import (
                period_bounds_utc,
                resolve_period,
            )

            anchor_row = (
                await db.execute(
                    select(RowTimestamp.timestamp)
                    .join(StagedRow, StagedRow.id == RowTimestamp.staged_row_id)
                    .where(StagedRow.source_id.in_(resolved_sources))
                    .where(*scope_clauses)
                    .order_by(RowTimestamp.timestamp.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            anchor = anchor_row.date() if anchor_row is not None else None
            resolved_period = resolve_period(
                period, anchor=anchor, count=period_count
            )
            start, end = period_bounds_utc(resolved_period)
            if start is not None and end is not None:
                in_window = (
                    select(RowTimestamp.staged_row_id)
                    .where(
                        RowTimestamp.staged_row_id == StagedRow.id,
                        RowTimestamp.timestamp >= start,
                        RowTimestamp.timestamp < end,
                    )
                    .exists()
                )
                rows_stmt = select(StagedRow).where(
                    StagedRow.source_id.in_(resolved_sources)
                ).where(*scope_clauses).where(in_window).limit(row_limit)

        staged = (await db.execute(rows_stmt)).scalars().all()

        rows: list[dict[str, Any]] = []
        for s in staged:
            row = dict(s.raw_data)
            row["__source_id"] = str(s.source_id)
            rows.append(row)

        instance = cls(
            rows=rows,
            column_map=column_map,
            source_ids=resolved_sources,
            concepts=concepts,
        )
        instance.period = resolved_period
        return instance

    @property
    def source_ids(self) -> list[uuid.UUID]:
        return list(self._source_ids)

    def concept(self, concept_key: str) -> ConceptMetadata | None:
        """Catalog metadata for a mapped concept, or None if it isn't here."""
        return self._concepts.get(concept_key)

    def values_for(
        self,
        concept_key: str,
        *,
        group_by: list[str] | None = None,
    ) -> ConceptValues:
        """
        Extract numeric values for a concept, optionally with grouping keys.

        `group_by` is a list of concept keys. For each row, we resolve each
        grouping concept to its column (per source), read the value, and
        stringify it as the group key. If a row's source doesn't have a
        mapping for one of the grouping concepts, that row is skipped with
        a skipped++ (we don't want inconsistent groups).

        If `group_by` is None or empty, returns ungrouped values.
        """
        result = ConceptValues()
        contributing_sources: set[uuid.UUID] = set()

        for row in self._rows:
            source_id_str = row.get("__source_id")
            if source_id_str is None:
                result.skipped += 1
                continue
            try:
                source_id = uuid.UUID(source_id_str)
            except (ValueError, TypeError):
                result.skipped += 1
                continue

            mapping = self._column_map.get(source_id)
            if not mapping:
                continue

            value_col = mapping.get(concept_key)
            if value_col is None:
                continue

            raw_value = row.get(value_col)
            fv = _to_float(raw_value)
            if fv is None:
                result.skipped += 1
                continue

            # Resolve grouping keys, if any.
            if group_by:
                group_values: list[str] = []
                resolved_all = True
                for gk in group_by:
                    gcol = mapping.get(gk)
                    if gcol is None:
                        resolved_all = False
                        break
                    group_values.append(_key_of(row.get(gcol)))
                if not resolved_all:
                    result.skipped += 1
                    continue
                result.keys.append("||".join(group_values))

            result.values.append(fv)
            contributing_sources.add(source_id)

        result.sources = sorted(contributing_sources, key=str)
        return result