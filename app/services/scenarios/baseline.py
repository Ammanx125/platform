"""
Resolve a tenant-owned, auditable revenue/COGS baseline for profit analysis.

V1 deliberately requires one source with confirmed Revenue, Cost of Goods
Sold, and Currency mappings. It will not combine unrelated periods, sources,
or generic operating costs and pretend they form gross profit.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.browser import BrowserExtractionProfile
from app.db.models.dataset import DataSource, IngestionJob, StagedRow
from app.db.models.semantic import SemanticMapping

REVENUE_CONCEPT = "Finance.Revenue"
COGS_CONCEPT = "Finance.CostOfGoodsSold"
CURRENCY_CONCEPT = "Finance.Currency"
SUPPORTED_SNAPSHOT_MODES = frozenset({"append", "replace_active"})
_MAX_ROWS = 200_000
_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")


class BaselineUnavailable(Exception):
    """The source cannot support a reliable, explainable profit scenario."""

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


@dataclass(frozen=True)
class ProfitBaseline:
    source_id: uuid.UUID
    source_name: str
    snapshot_mode: str
    ingestion_job_ids: list[uuid.UUID]
    revenue_concept: str
    revenue_column: str
    cogs_concept: str
    cogs_column: str
    currency_concept: str
    currency_column: str
    currency: str
    revenue: Decimal
    cogs: Decimal
    rows_considered: int
    latest_ingested_at: datetime | None

    @property
    def gross_profit(self) -> Decimal:
        return self.revenue - self.cogs

    @property
    def gross_margin_pct(self) -> Decimal | None:
        if self.revenue == 0:
            return None
        return self.gross_profit / self.revenue * Decimal(100)

    def to_snapshot(self) -> dict[str, Any]:
        return {
            "source_id": str(self.source_id),
            "source_name": self.source_name,
            "snapshot_mode": self.snapshot_mode,
            "ingestion_job_ids": [str(job_id) for job_id in self.ingestion_job_ids],
            "revenue_concept": self.revenue_concept,
            "revenue_column": self.revenue_column,
            "cogs_concept": self.cogs_concept,
            "cogs_column": self.cogs_column,
            "currency_concept": self.currency_concept,
            "currency_column": self.currency_column,
            "currency": self.currency,
            "revenue": str(self.revenue),
            "cogs": str(self.cogs),
            "gross_profit": str(self.gross_profit),
            "gross_margin_pct": (
                str(self.gross_margin_pct) if self.gross_margin_pct is not None else None
            ),
            "rows_considered": self.rows_considered,
            "rows_skipped": 0,
            "skipped_examples": [],
            "latest_ingested_at": (
                self.latest_ingested_at.isoformat() if self.latest_ingested_at is not None else None
            ),
        }


async def list_candidate_sources(db: AsyncSession, *, tenant_id: uuid.UUID) -> list[DataSource]:
    statement = (
        select(DataSource)
        .where(
            DataSource.tenant_id == tenant_id,
            DataSource.is_active.is_(True),
        )
        .order_by(DataSource.created_at.desc())
    )
    return list((await db.execute(statement)).scalars().all())


async def _mapping(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    concept: str,
    confirmed_only: bool,
) -> SemanticMapping | None:
    statement = select(SemanticMapping).where(
        SemanticMapping.tenant_id == tenant_id,
        SemanticMapping.source_id == source_id,
        SemanticMapping.canonical_concept_key == concept,
    )
    if confirmed_only:
        statement = statement.where(SemanticMapping.status == "confirmed")
    return (
        await db.execute(statement.order_by(SemanticMapping.created_at.asc()).limit(1))
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


def _parse_number(raw: Any, *, field_name: str, row_number: int) -> Decimal:
    if raw is None or isinstance(raw, bool):
        raise BaselineUnavailable(
            "incomplete_data",
            f"row {row_number} has no usable {field_name} value; "
            "the calculation was blocked rather than silently omitting data",
        )
    try:
        value = Decimal(str(raw).strip().replace(",", ""))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise BaselineUnavailable(
            "invalid_data",
            f"row {row_number} has an invalid {field_name} value",
        ) from exc
    if not value.is_finite():
        raise BaselineUnavailable(
            "invalid_data", f"row {row_number} has a non-finite {field_name} value"
        )
    if value < 0:
        raise BaselineUnavailable(
            "negative_financial_value",
            f"row {row_number} has negative {field_name}; "
            "refund and credit treatment must be defined before inclusion",
        )
    return value


async def resolve_profit_baseline(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
) -> ProfitBaseline:
    source = (
        await db.execute(
            select(DataSource).where(
                DataSource.id == source_id,
                DataSource.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if source is None:
        raise BaselineUnavailable("source_not_found", "source not found for this tenant")
    if not source.is_active:
        raise BaselineUnavailable("source_inactive", "the source is inactive")
    if source.snapshot_mode not in SUPPORTED_SNAPSHOT_MODES:
        raise BaselineUnavailable(
            "unsupported_snapshot_mode",
            f"snapshot mode {source.snapshot_mode!r} is not supported",
        )

    required = (REVENUE_CONCEPT, COGS_CONCEPT, CURRENCY_CONCEPT)
    mappings: dict[str, SemanticMapping] = {}
    missing: list[str] = []
    unconfirmed: list[str] = []
    for concept in required:
        confirmed = await _mapping(
            db,
            tenant_id=tenant_id,
            source_id=source.id,
            concept=concept,
            confirmed_only=True,
        )
        if confirmed is not None:
            mappings[concept] = confirmed
            continue
        any_mapping = await _mapping(
            db,
            tenant_id=tenant_id,
            source_id=source.id,
            concept=concept,
            confirmed_only=False,
        )
        (unconfirmed if any_mapping is not None else missing).append(concept)
    if missing or unconfirmed:
        unmet = sorted(set(missing + unconfirmed))
        raise BaselineUnavailable(
            "missing_mappings",
            "a reliable profit baseline requires confirmed mappings for: " + ", ".join(unmet),
            missing=unmet,
        )

    active_job_id = await _active_job_id(db, tenant_id=tenant_id, source=source)
    if source.snapshot_mode == "replace_active" and active_job_id is None:
        raise BaselineUnavailable(
            "no_active_snapshot",
            "the replace_active source has no active ingestion snapshot",
        )

    statement = (
        select(StagedRow.raw_data, StagedRow.row_number, StagedRow.job_id)
        .join(IngestionJob, IngestionJob.id == StagedRow.job_id)
        .where(
            StagedRow.tenant_id == tenant_id,
            StagedRow.source_id == source.id,
            IngestionJob.status == "succeeded",
        )
    )
    if active_job_id is not None:
        statement = statement.where(StagedRow.job_id == active_job_id)
    from app.services.datasets.scope import deleted_snapshot_filter

    statement = statement.where(
        *await deleted_snapshot_filter(db, tenant_id=tenant_id)
    )
    statement = statement.order_by(StagedRow.job_id, StagedRow.row_number).limit(_MAX_ROWS + 1)
    staged = (await db.execute(statement)).all()
    if len(staged) > _MAX_ROWS:
        raise BaselineUnavailable(
            "too_many_rows",
            f"the source exceeds the {_MAX_ROWS:,}-row v1 calculation limit",
        )
    if not staged:
        raise BaselineUnavailable("no_staged_rows", "the source has no successful staged rows")

    revenue_column = mappings[REVENUE_CONCEPT].source_column
    cogs_column = mappings[COGS_CONCEPT].source_column
    currency_column = mappings[CURRENCY_CONCEPT].source_column
    total_revenue = Decimal(0)
    total_cogs = Decimal(0)
    currencies: set[str] = set()
    job_ids: set[uuid.UUID] = set()
    for raw_data, row_number, job_id in staged:
        data = raw_data if isinstance(raw_data, dict) else {}
        row_label = int(row_number)
        revenue = _parse_number(
            data.get(revenue_column),
            field_name="revenue",
            row_number=row_label,
        )
        cogs = _parse_number(
            data.get(cogs_column),
            field_name="COGS",
            row_number=row_label,
        )
        raw_currency = data.get(currency_column)
        currency = str(raw_currency or "").strip().upper()
        if not _CURRENCY_PATTERN.fullmatch(currency):
            raise BaselineUnavailable(
                "unknown_currency",
                f"row {row_label} has no normalized three-letter currency code; "
                "currency must be confirmed before combining financial values",
                missing=[CURRENCY_CONCEPT],
            )
        currencies.add(currency)
        if len(currencies) > 1:
            raise BaselineUnavailable(
                "mixed_currencies",
                "the source contains multiple currencies; currency conversion "
                "is not modeled, so the values cannot be combined",
            )
        total_revenue += revenue
        total_cogs += cogs
        job_ids.add(job_id)

    latest_ingested_at = (
        await db.execute(
            select(func.max(IngestionJob.finished_at)).where(IngestionJob.id.in_(job_ids))
        )
    ).scalar_one_or_none()
    return ProfitBaseline(
        source_id=source.id,
        source_name=source.name,
        snapshot_mode=source.snapshot_mode,
        ingestion_job_ids=sorted(job_ids, key=str),
        revenue_concept=REVENUE_CONCEPT,
        revenue_column=revenue_column,
        cogs_concept=COGS_CONCEPT,
        cogs_column=cogs_column,
        currency_concept=CURRENCY_CONCEPT,
        currency_column=currency_column,
        currency=next(iter(currencies)),
        revenue=total_revenue,
        cogs=total_cogs,
        rows_considered=len(staged),
        latest_ingested_at=latest_ingested_at,
    )


def is_baseline_current(
    *, saved_snapshot: dict[str, Any], current_snapshot: dict[str, Any]
) -> bool:
    return saved_snapshot == current_snapshot


__all__ = [
    "BaselineUnavailable",
    "ProfitBaseline",
    "REVENUE_CONCEPT",
    "COGS_CONCEPT",
    "CURRENCY_CONCEPT",
    "is_baseline_current",
    "list_candidate_sources",
    "resolve_profit_baseline",
]
