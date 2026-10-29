# app/services/scenarios/baseline.py
"""
Baseline resolution: turn one tenant-owned data source into the numbers a
scenario needs, or explain precisely why it cannot.

Design rules (decided for Scenario Lab v1):

  - One source per scenario. That removes the append/replace_active
    mixing problem entirely instead of pretending it does not exist.
  - Single-source does not by itself guarantee coherent snapshots, so the
    source's snapshot_mode is validated explicitly and its active
    ingestion job (if any) is recorded as provenance.
  - Only *confirmed* semantic mappings are used. A proposed mapping is a
    guess; financial output must not rest on a guess.
  - The baseline is computed from raw staged values, not from the KPI
    service. The existing ``procurement.total_spend`` KPI sums
    ``Procurement.PurchasePrice`` (a per-unit price), which is not spend.
    Spend is quantity x unit price, so this module resolves the columns
    itself and keeps the arithmetic in engine.py.

Nothing here writes to source data, and nothing here calls a model.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.browser import BrowserExtractionProfile
from app.db.models.dataset import DataSource, IngestionJob, StagedRow
from app.db.models.semantic import SemanticMapping
from app.services.analytics.context import _to_float
from app.services.scenarios.engine import SupplierSpend

# Concept keys the supplier-spend scenario needs.
SUPPLIER_CONCEPT = "Procurement.Supplier"
PRICE_CONCEPT = "Procurement.PurchasePrice"
QUANTITY_CONCEPT = "Common.Quantity"
# Some sources carry one total-per-line column instead of quantity x price.
# Either shape is accepted; neither is preferred silently.
AMOUNT_CONCEPTS: tuple[str, ...] = ("Procurement.PurchaseAmount", "Common.Amount")

SUPPORTED_SNAPSHOT_MODES = frozenset({"append", "replace_active"})
UNKNOWN_SUPPLIER = "(unspecified supplier)"

# Above this many staged rows the baseline stops being a quick preview.
_MAX_ROWS = 200_000


class BaselineUnavailable(Exception):
    """
    The scenario cannot honestly run against this source.

    ``code`` is a stable machine-readable reason; ``detail`` is for humans.
    ``missing`` lists the concept keys that need a confirmed mapping.
    """

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        missing: list[str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.missing = missing or []


@dataclass
class SupplierBaseline:
    """The verified inputs to a supplier-spend simulation, plus provenance."""
    source_id: uuid.UUID
    source_name: str
    snapshot_mode: str
    ingestion_job_ids: list[uuid.UUID]
    supplier_concept_column: str
    spend_shape: str  # "quantity_x_price" | "amount_column"
    spend_columns: dict[str, str]
    suppliers: list[SupplierSpend]
    rows_considered: int
    rows_skipped: int
    skipped_examples: list[str] = field(default_factory=list)
    latest_ingested_at: datetime | None = None

    @property
    def total_spend(self) -> Decimal:
        return sum((s.baseline_spend for s in self.suppliers), Decimal(0))

    def to_snapshot(self) -> dict[str, Any]:
        """
        The immutable record saved with a scenario. Deliberately compact:
        totals and provenance, never the underlying business rows.
        """
        return {
            "source_id": str(self.source_id),
            "source_name": self.source_name,
            "snapshot_mode": self.snapshot_mode,
            "ingestion_job_ids": [str(j) for j in self.ingestion_job_ids],
            "supplier_concept_column": self.supplier_concept_column,
            "spend_shape": self.spend_shape,
            "spend_columns": dict(self.spend_columns),
            "total_spend": str(self.total_spend),
            "supplier_count": len(self.suppliers),
            "suppliers": [
                {"supplier_key": s.supplier_key, "baseline_spend": str(s.baseline_spend)}
                for s in self.suppliers
            ],
            "rows_considered": self.rows_considered,
            "rows_skipped": self.rows_skipped,
            "skipped_examples": list(self.skipped_examples),
            "latest_ingested_at": (
                self.latest_ingested_at.isoformat()
                if self.latest_ingested_at is not None
                else None
            ),
        }


async def list_candidate_sources(
    db: AsyncSession, *, tenant_id: uuid.UUID
) -> list[DataSource]:
    """Active sources that could plausibly carry spend data. Read-only."""
    stmt = (
        select(DataSource)
        .where(
            DataSource.tenant_id == tenant_id,
            DataSource.is_active.is_(True),
        )
        .order_by(DataSource.created_at.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def _confirmed_mapping(
    db: AsyncSession, *, tenant_id: uuid.UUID, source_id: uuid.UUID, concept: str
) -> SemanticMapping | None:
    return (
        await db.execute(
            select(SemanticMapping)
            .where(
                SemanticMapping.tenant_id == tenant_id,
                SemanticMapping.source_id == source_id,
                SemanticMapping.canonical_concept_key == concept,
                SemanticMapping.status == "confirmed",
            )
            .order_by(SemanticMapping.created_at.asc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _any_mapping(
    db: AsyncSession, *, tenant_id: uuid.UUID, source_id: uuid.UUID, concept: str
) -> SemanticMapping | None:
    return (
        await db.execute(
            select(SemanticMapping)
            .where(
                SemanticMapping.tenant_id == tenant_id,
                SemanticMapping.source_id == source_id,
                SemanticMapping.canonical_concept_key == concept,
            )
            .limit(1)
        )
    ).scalar_one_or_none()


async def _active_job_id(
    db: AsyncSession, *, tenant_id: uuid.UUID, source: DataSource
) -> uuid.UUID | None:
    if source.snapshot_mode != "replace_active":
        return None
    return (
        await db.execute(
            select(BrowserExtractionProfile.active_ingestion_job_id).where(
                BrowserExtractionProfile.tenant_id == tenant_id,
                BrowserExtractionProfile.source_id == source.id,
            )
        )
    ).scalar_one_or_none()


def _parse_decimal(raw: Any) -> Decimal | None:
    """
    Strict money parsing. Reuses the platform's lenient coercion (currency
    symbols and thousands separators) but returns Decimal and rejects NaN
    and infinity, which float parsing happily produces.
    """
    if raw is None or isinstance(raw, bool):
        return None
    try:
        if isinstance(raw, (int, float)):
            value = Decimal(str(raw))
        else:
            text = str(raw).strip()
            cleaned = (
                text.replace(",", "")
                .replace("$", "")
                .replace("\u20ac", "")
                .replace("\u00a3", "")
                .replace("Br", "")
                .strip()
            )
            if not cleaned:
                return None
            value = Decimal(cleaned)
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not value.is_finite():
        return None
    return value


async def resolve_supplier_baseline(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
) -> SupplierBaseline:
    """
    Build the supplier spend baseline for one source.

    Raises BaselineUnavailable with a specific code for every reason the
    scenario cannot run. Never substitutes mock numbers.
    """
    source = (
        await db.execute(
            select(DataSource).where(
                DataSource.id == source_id,
                DataSource.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if source is None:
        # Same answer for "does not exist" and "someone else's": the API
        # maps this to 404 without leaking which.
        raise BaselineUnavailable(
            "source_not_found", "source not found for this tenant"
        )
    if not source.is_active:
        raise BaselineUnavailable(
            "source_inactive",
            f"source {source.name!r} is inactive and is excluded from current analytics",
        )
    if source.snapshot_mode not in SUPPORTED_SNAPSHOT_MODES:
        raise BaselineUnavailable(
            "unsupported_snapshot_mode",
            f"snapshot mode {source.snapshot_mode!r} is not supported by scenarios",
        )

    supplier_mapping = await _confirmed_mapping(
        db, tenant_id=tenant_id, source_id=source.id, concept=SUPPLIER_CONCEPT
    )
    price_mapping = await _confirmed_mapping(
        db, tenant_id=tenant_id, source_id=source.id, concept=PRICE_CONCEPT
    )
    quantity_mapping = await _confirmed_mapping(
        db, tenant_id=tenant_id, source_id=source.id, concept=QUANTITY_CONCEPT
    )

    # Spend shape: quantity x unit price, or one amount-per-line column.
    amount_mapping: SemanticMapping | None = None
    for concept in AMOUNT_CONCEPTS:
        amount_mapping = await _confirmed_mapping(
            db, tenant_id=tenant_id, source_id=source.id, concept=concept
        )
        if amount_mapping is not None:
            break

    # Required concepts, decided in one place so the error can name them all:
    #   supplier                 - always
    #   unit price + quantity    - when the source is line-item shaped
    #   amount                   - when the source is total-per-line shaped
    if price_mapping is not None and quantity_mapping is None:
        required: list[str] = [SUPPLIER_CONCEPT, QUANTITY_CONCEPT]
    elif amount_mapping is None:
        required = [SUPPLIER_CONCEPT, PRICE_CONCEPT]
    else:
        required = [SUPPLIER_CONCEPT]

    missing: list[str] = []
    unconfirmed: list[str] = []
    for concept in required:
        if await _confirmed_mapping(
            db, tenant_id=tenant_id, source_id=source.id, concept=concept
        ) is not None:
            continue
        if await _any_mapping(
            db, tenant_id=tenant_id, source_id=source.id, concept=concept
        ) is None:
            missing.append(concept)
        else:
            unconfirmed.append(concept)

    if missing or unconfirmed:
        parts: list[str] = []
        if missing:
            parts.append("no mapping exists for " + ", ".join(missing))
        if unconfirmed:
            parts.append(
                "confirmed mapping required for "
                + ", ".join(unconfirmed)
                + " (a proposal is not enough for a financial calculation)"
            )
        raise BaselineUnavailable(
            "missing_mappings",
            "this source cannot be simulated yet: " + "; ".join(parts),
            missing=sorted(set(missing + unconfirmed)),
        )

    assert supplier_mapping is not None
    supplier_column = supplier_mapping.source_column

    spend_shape: str
    spend_columns: dict[str, str]
    if quantity_mapping is not None and price_mapping is not None:
        spend_shape = "quantity_x_price"
        spend_columns = {
            "quantity": quantity_mapping.source_column,
            "unit_price": price_mapping.source_column,
        }
    elif amount_mapping is not None and (
        quantity_mapping is None or price_mapping is not None
    ):
        spend_shape = "amount_column"
        spend_columns = {"amount": amount_mapping.source_column}
    else:
        # quantity + amount with no unit price: quantity is redundant and
        # the source's unit price is unverified. Refuse rather than guess.
        raise BaselineUnavailable(
            "ambiguous_spend_shape",
            "this source has quantity and amount columns but no confirmed unit "
            "price; Sansa will not guess which value is the spend",
        )

    scope_clauses = []
    active_job_id = await _active_job_id(db, tenant_id=tenant_id, source=source)
    if source.snapshot_mode == "replace_active":
        if active_job_id is None:
            raise BaselineUnavailable(
                "no_active_snapshot",
                f"source {source.name!r} uses replace_active snapshots but has no "
                "active snapshot yet",
            )
        scope_clauses = [StagedRow.job_id == active_job_id]

    rows_stmt = (
        select(StagedRow.raw_data, StagedRow.job_id)
        .where(
            StagedRow.tenant_id == tenant_id,
            StagedRow.source_id == source.id,
            *scope_clauses,
        )
        .limit(_MAX_ROWS + 1)
    )
    staged = (await db.execute(rows_stmt)).all()
    if len(staged) > _MAX_ROWS:
        raise BaselineUnavailable(
            "too_many_rows",
            f"this source has more than {_MAX_ROWS:,} staged rows; the v1 "
            "simulator refuses to aggregate that many rows in memory",
        )
    if not staged:
        raise BaselineUnavailable(
            "no_staged_rows",
            f"source {source.name!r} has no staged rows yet; ingest it before "
            "running a scenario",
        )

    by_supplier: dict[str, Decimal] = {}
    rows_skipped = 0
    skipped_examples: list[str] = []
    for raw_data, _job_id in staged:
        data = raw_data or {}
        raw_supplier = data.get(supplier_column)
        supplier_key = str(raw_supplier).strip() if raw_supplier not in (None, "") else ""
        if not supplier_key:
            supplier_key = UNKNOWN_SUPPLIER

        if spend_shape == "quantity_x_price":
            quantity = _parse_decimal(data.get(spend_columns["quantity"]))
            unit_price = _parse_decimal(data.get(spend_columns["unit_price"]))
            if quantity is None or unit_price is None:
                rows_skipped += 1
                if len(skipped_examples) < 5:
                    skipped_examples.append(
                        f"{supplier_key}: quantity={data.get(spend_columns['quantity'])!r}, "
                        f"unit_price={data.get(spend_columns['unit_price'])!r}"
                    )
                continue
            line_spend = quantity * unit_price
        else:
            amount = _parse_decimal(data.get(spend_columns["amount"]))
            if amount is None:
                rows_skipped += 1
                if len(skipped_examples) < 5:
                    skipped_examples.append(
                        f"{supplier_key}: amount={data.get(spend_columns['amount'])!r}"
                    )
                continue
            line_spend = amount

        if line_spend < 0:
            # A negative line would make total spend meaningless as a
            # baseline (credits, refunds, corrections). Skip and report.
            rows_skipped += 1
            if len(skipped_examples) < 5:
                skipped_examples.append(
                    f"{supplier_key}: negative line spend {line_spend}"
                )
            continue

        by_supplier[supplier_key] = by_supplier.get(supplier_key, Decimal(0)) + line_spend

    if not by_supplier:
        raise BaselineUnavailable(
            "no_usable_spend_rows",
            f"none of the {len(staged)} staged rows in {source.name!r} produced a "
            "usable spend value with the confirmed mappings",
        )

    suppliers = [
        SupplierSpend(supplier_key=key, baseline_spend=value)
        for key, value in sorted(by_supplier.items())
    ]
    suppliers.sort(key=lambda s: (-s.baseline_spend, s.supplier_key))

    ingestion_job_ids = [active_job_id] if active_job_id is not None else [
        row[1] for row in staged
    ]
    latest_ingested_at = (
        await db.execute(
            select(func.max(IngestionJob.finished_at)).where(
                IngestionJob.id.in_(ingestion_job_ids),
            )
        )
    ).scalar_one_or_none()

    return SupplierBaseline(
        source_id=source.id,
        source_name=source.name,
        snapshot_mode=source.snapshot_mode,
        ingestion_job_ids=sorted(set(ingestion_job_ids), key=str),
        supplier_concept_column=supplier_column,
        spend_shape=spend_shape,
        spend_columns=spend_columns,
        suppliers=suppliers,
        rows_considered=len(staged),
        rows_skipped=rows_skipped,
        skipped_examples=skipped_examples,
        latest_ingested_at=latest_ingested_at,
    )


def is_baseline_current(
    *, saved_job_ids: list[str], current_job_ids: list[str]
) -> bool:
    """
    A saved scenario is stale when the set of ingestion jobs its baseline
    was computed from no longer matches what the source would produce now.
    """
    return sorted(saved_job_ids) == sorted(current_job_ids)


__all__ = [
    "BaselineUnavailable",
    "SupplierBaseline",
    "SUPPLIER_CONCEPT",
    "PRICE_CONCEPT",
    "QUANTITY_CONCEPT",
    "_to_float",  # re-exported for parity with analytics coercion tests
]
