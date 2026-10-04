"""Deterministic RFM and revenue-trend analysis over semantically mapped rows."""
from __future__ import annotations

import math
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.dataset import DataSource, StagedRow
from app.db.models.semantic import SemanticMapping

CUSTOMER_CONCEPT = "Sales.Customer"
TRANSACTION_CONCEPT = "Sales.TransactionId"
DATE_CONCEPT = "Sales.TransactionDate"
REVENUE_CONCEPT = "Finance.Revenue"
REQUIRED_CONCEPTS = (
    CUSTOMER_CONCEPT,
    TRANSACTION_CONCEPT,
    DATE_CONCEPT,
    REVENUE_CONCEPT,
)
SEGMENTS = (
    "champions",
    "loyal",
    "growing",
    "declining",
    "at_risk",
    "inactive",
    "low_value",
)


@dataclass(frozen=True)
class CustomerBehaviorThresholds:
    recent_days: int = 30
    at_risk_days: int = 90
    inactive_days: int = 180
    min_purchase_count: int = 3
    champion_purchase_count: int = 5
    trend_threshold_pct: float = 20.0
    high_value_percentile: float = 75.0


@dataclass(frozen=True)
class CustomerTransaction:
    customer_key: str
    transaction_id: str
    transaction_date: date
    revenue: float
    source_id: str


@dataclass
class CustomerBehaviorProfile:
    customer_key: str
    last_purchase_date: date
    recency_days: int
    purchase_count: int
    total_revenue: float
    recent_revenue: float
    previous_revenue: float
    trend_pct: float | None
    segment: str
    high_value: bool


@dataclass
class CustomerBehaviorAnalysis:
    as_of_date: date
    customer_count: int
    transaction_count: int
    total_revenue: float
    at_risk_count: int
    inactive_count: int
    high_value_declining_count: int
    top_10_revenue_share_pct: float | None
    segment_counts: dict[str, int]
    customers: list[CustomerBehaviorProfile]
    rows_skipped: int


class CustomerBehaviorDataError(ValueError):
    """Customer behavior could not be calculated from the available rows."""


def _configured_thresholds() -> CustomerBehaviorThresholds:
    return CustomerBehaviorThresholds(
        recent_days=settings.customer_behavior_recent_days,
        at_risk_days=settings.customer_behavior_at_risk_days,
        inactive_days=settings.customer_behavior_inactive_days,
        min_purchase_count=settings.customer_behavior_min_purchase_count,
        champion_purchase_count=settings.customer_behavior_champion_purchase_count,
        trend_threshold_pct=settings.customer_behavior_trend_threshold_pct,
        high_value_percentile=settings.customer_behavior_high_value_percentile,
    )


def _normalized_key(value: str) -> str:
    return " ".join(value.strip().split()).casefold()


def _revenue_percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(percentile / 100 * len(ordered)) - 1)
    return ordered[index]


def analyze_customer_behavior(
    transactions: list[CustomerTransaction],
    *,
    as_of_date: date | None = None,
    thresholds: CustomerBehaviorThresholds | None = None,
    rows_skipped: int = 0,
) -> CustomerBehaviorAnalysis:
    """Build RFM profiles after consolidating line items into unique orders."""
    if not transactions:
        raise CustomerBehaviorDataError("no complete customer transactions were found")
    thresholds = thresholds or _configured_thresholds()
    as_of = as_of_date or datetime.now(UTC).date()

    orders: dict[tuple[str, str, str], dict[str, Any]] = {}
    customer_labels: dict[str, str] = {}
    for tx in transactions:
        normalized_customer = _normalized_key(tx.customer_key)
        normalized_order = _normalized_key(tx.transaction_id)
        if not normalized_customer or not normalized_order:
            continue
        customer_labels.setdefault(normalized_customer, tx.customer_key.strip())
        order_key = (tx.source_id, normalized_customer, normalized_order)
        order = orders.get(order_key)
        if order is None:
            orders[order_key] = {
                "customer": normalized_customer,
                "date": tx.transaction_date,
                "revenue": tx.revenue,
            }
        else:
            order["date"] = max(order["date"], tx.transaction_date)
            order["revenue"] += tx.revenue

    if not orders:
        raise CustomerBehaviorDataError("no complete customer transactions were found")

    by_customer: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for order in orders.values():
        by_customer[order["customer"]].append(order)

    totals = {
        customer: sum(order["revenue"] for order in customer_orders)
        for customer, customer_orders in by_customer.items()
    }
    high_value_floor = _revenue_percentile(
        list(totals.values()), thresholds.high_value_percentile
    )

    profiles: list[CustomerBehaviorProfile] = []
    recent_window = thresholds.recent_days
    for customer, customer_orders in by_customer.items():
        last_date = max(order["date"] for order in customer_orders)
        recency = max((as_of - last_date).days, 0)
        recent_revenue = sum(
            order["revenue"]
            for order in customer_orders
            if 0 <= (as_of - order["date"]).days <= recent_window
        )
        previous_revenue = sum(
            order["revenue"]
            for order in customer_orders
            if recent_window < (as_of - order["date"]).days <= 2 * recent_window
        )
        trend_pct = (
            (recent_revenue - previous_revenue) / previous_revenue * 100
            if previous_revenue > 0
            else None
        )
        high_value = totals[customer] >= high_value_floor

        if recency > thresholds.inactive_days:
            segment = "inactive"
        elif recency > thresholds.at_risk_days and (
            len(customer_orders) >= thresholds.min_purchase_count
        ):
            segment = "at_risk"
        elif (
            trend_pct is not None
            and trend_pct <= -thresholds.trend_threshold_pct
        ):
            segment = "declining"
        elif (
            high_value
            and recency <= recent_window
            and len(customer_orders) >= thresholds.champion_purchase_count
        ):
            segment = "champions"
        elif (
            trend_pct is not None
            and trend_pct >= thresholds.trend_threshold_pct
        ):
            segment = "growing"
        elif (
            len(customer_orders) >= thresholds.min_purchase_count
            and recency <= thresholds.at_risk_days
        ):
            segment = "loyal"
        else:
            segment = "low_value"

        profiles.append(CustomerBehaviorProfile(
            customer_key=customer_labels[customer],
            last_purchase_date=last_date,
            recency_days=recency,
            purchase_count=len(customer_orders),
            total_revenue=totals[customer],
            recent_revenue=recent_revenue,
            previous_revenue=previous_revenue,
            trend_pct=trend_pct,
            segment=segment,
            high_value=high_value,
        ))

    segment_counts = {segment: 0 for segment in SEGMENTS}
    for profile in profiles:
        segment_counts[profile.segment] += 1

    total_revenue = sum(totals.values())
    top_ten_revenue = sum(
        sorted(totals.values(), reverse=True)[:10]
    )
    concentration = (
        top_ten_revenue / total_revenue * 100
        if total_revenue > 0
        else None
    )
    segment_order = {segment: index for index, segment in enumerate(SEGMENTS)}
    profiles.sort(
        key=lambda profile: (
            segment_order[profile.segment],
            -profile.total_revenue,
            profile.customer_key.casefold(),
        )
    )

    return CustomerBehaviorAnalysis(
        as_of_date=as_of,
        customer_count=len(profiles),
        transaction_count=len(orders),
        total_revenue=total_revenue,
        at_risk_count=segment_counts["at_risk"],
        inactive_count=segment_counts["inactive"],
        high_value_declining_count=sum(
            profile.high_value and profile.segment == "declining"
            for profile in profiles
        ),
        top_10_revenue_share_pct=concentration,
        segment_counts=segment_counts,
        customers=profiles,
        rows_skipped=rows_skipped,
    )


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        try:
            return datetime.fromisoformat(cleaned.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return date.fromisoformat(cleaned[:10])
            except ValueError:
                return None
    return None


def _parse_revenue(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        normalized = str(value).replace(",", "").strip()
        for symbol in ("$", "€", "£", "¥"):
            normalized = normalized.replace(symbol, "")
        amount = float(normalized)
    except (TypeError, ValueError):
        return None
    return amount if math.isfinite(amount) else None


async def analyze_tenant_customer_behavior(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_ids: list[uuid.UUID] | None = None,
    limit: int | None = None,
    as_of_date: date | None = None,
) -> CustomerBehaviorAnalysis | None:
    """Load confirmed customer transaction mappings for a tenant and analyze them."""
    source_stmt = select(DataSource.id).where(
        DataSource.tenant_id == tenant_id,
        DataSource.is_active.is_(True),
    )
    if source_ids is not None:
        source_stmt = source_stmt.where(DataSource.id.in_(source_ids))
    active_source_ids = list((await db.execute(source_stmt)).scalars().all())
    if not active_source_ids:
        return None

    mapping_rows = (
        await db.execute(
            select(SemanticMapping.source_id, SemanticMapping.canonical_concept_key,
                   SemanticMapping.source_column)
            .where(
                SemanticMapping.tenant_id == tenant_id,
                SemanticMapping.source_id.in_(active_source_ids),
                SemanticMapping.status == "confirmed",
                SemanticMapping.canonical_concept_key.in_(REQUIRED_CONCEPTS),
            )
        )
    ).all()
    columns: dict[uuid.UUID, dict[str, str]] = defaultdict(dict)
    for mapped_source_id, concept, column in mapping_rows:
        columns[mapped_source_id][concept] = column
    eligible_source_ids = [
        source_id for source_id, source_columns in columns.items()
        if all(concept in source_columns for concept in REQUIRED_CONCEPTS)
    ]
    if not eligible_source_ids:
        return None

    row_limit = limit or settings.customer_behavior_row_limit
    rows = list(
        (
            await db.execute(
                select(StagedRow)
                .where(
                    StagedRow.tenant_id == tenant_id,
                    StagedRow.source_id.in_(eligible_source_ids),
                )
                .order_by(StagedRow.created_at, StagedRow.row_number)
                .limit(row_limit + 1)
            )
        ).scalars().all()
    )
    if len(rows) > row_limit:
        raise CustomerBehaviorDataError(
            f"customer analysis exceeds the configured {row_limit:,}-row limit"
        )

    transactions: list[CustomerTransaction] = []
    rows_skipped = 0
    for row in rows:
        source_columns = columns[row.source_id]
        customer = str(row.raw_data.get(source_columns[CUSTOMER_CONCEPT]) or "").strip()
        transaction_id = str(
            row.raw_data.get(source_columns[TRANSACTION_CONCEPT]) or ""
        ).strip()
        transaction_date = _parse_date(
            row.raw_data.get(source_columns[DATE_CONCEPT])
        )
        revenue = _parse_revenue(
            row.raw_data.get(source_columns[REVENUE_CONCEPT])
        )
        if not customer or not transaction_id or transaction_date is None or revenue is None:
            rows_skipped += 1
            continue
        transactions.append(CustomerTransaction(
            customer_key=customer,
            transaction_id=transaction_id,
            transaction_date=transaction_date,
            revenue=revenue,
            source_id=str(row.source_id),
        ))

    if not transactions:
        return None
    return analyze_customer_behavior(
        transactions,
        as_of_date=as_of_date,
        rows_skipped=rows_skipped,
    )


def to_summary_dict(analysis: CustomerBehaviorAnalysis) -> dict[str, Any]:
    """Return a JSON-serializable summary payload, retaining typed profiles."""
    result = {
        "as_of_date": analysis.as_of_date,
        "customer_count": analysis.customer_count,
        "transaction_count": analysis.transaction_count,
        "total_revenue": analysis.total_revenue,
        "at_risk_count": analysis.at_risk_count,
        "inactive_count": analysis.inactive_count,
        "high_value_declining_count": analysis.high_value_declining_count,
        "top_10_revenue_share_pct": analysis.top_10_revenue_share_pct,
        "segment_counts": analysis.segment_counts,
        "customers": [
            {
                **asdict(profile),
                "last_purchase_date": profile.last_purchase_date,
            }
            for profile in analysis.customers
        ],
        "rows_skipped": analysis.rows_skipped,
    }
    return result
