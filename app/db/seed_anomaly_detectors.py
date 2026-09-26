# app/db/seed_anomaly_detectors.py
"""
Base anomaly detector catalog.

Global (not tenant-scoped). Idempotent, matching seed_concepts / seed_kpis.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.analytics import AnomalyDetectorDefinition

DEFAULT_TIME_PREF = ["content", "stream", "file", "ingestion"]

# (key, display_name, domain, value_concept, group_by_concept,
#  detector, parameters, severity, description)
DETECTORS: list[tuple] = [
    (
        "procurement.purchase_price_spikes",
        "Purchase Price Spikes",
        "Procurement",
        "Procurement.PurchasePrice",
        "Procurement.Supplier",
        "zscore_rolling",
        {"k": 3.0, "window": 7},
        "warning",
        "Rolling z-score on per-supplier purchase prices.",
    ),
    (
        "inventory.stock_level_drops",
        "Stock Level Drops",
        "Inventory",
        "Inventory.StockLevel",
        "Inventory.Product",
        "zscore_rolling",
        {"k": 3.0, "window": 7},
        "warning",
        "Rolling z-score on per-product stock levels.",
    ),
    (
        "sales.revenue_swings",
        "Revenue Swings",
        "Sales",
        "Finance.Revenue",
        "Sales.Customer",
        "zscore_global",
        {"k": 3.0},
        "info",
        "Global z-score on per-customer revenue; flags unusual customers.",
    ),
    (
        "operations.downtime_spikes",
        "Downtime Spikes",
        "Operations",
        "Operations.Downtime",
        "Management.BusinessUnit",
        "iqr",
        {"k": 1.5},
        "warning",
        "IQR-based detection of downtime outliers per business unit.",
    ),
]


async def seed_anomaly_detectors(db: AsyncSession) -> int:
    """
    Reconcile AnomalyDetectorDefinition rows. Inserts and updates. Never
    deletes. Returns the number of definitions reconciled.
    """
    existing = {
        d.key: d
        for d in (
            await db.execute(select(AnomalyDetectorDefinition))
        ).scalars().all()
    }

    reconciled = 0
    for (
        key, display_name, domain, value_concept, group_by_concept,
        detector, parameters, severity, description,
    ) in DETECTORS:
        if key in existing:
            d = existing[key]
            d.display_name = display_name
            d.domain = domain
            d.value_concept = value_concept
            d.group_by_concept = group_by_concept
            d.detector = detector
            d.parameters = parameters
            d.severity = severity
            d.description = description
        else:
            db.add(AnomalyDetectorDefinition(
                key=key,
                display_name=display_name,
                domain=domain,
                value_concept=value_concept,
                group_by_concept=group_by_concept,
                detector=detector,
                parameters=parameters,
                time_basis_preference=DEFAULT_TIME_PREF,
                severity=severity,
                is_enabled=True,
                description=description,
            ))
        reconciled += 1

    await db.flush()
    return reconciled