# app/db/seed_kpis.py
"""
Base KPI catalog.

Global (not tenant-scoped). Idempotent, matching the pattern of
seed_concepts and seed_packs.

Every formula references concepts that exist in the base catalog
(seed_concepts.py). If a formula references a concept that doesn't exist,
that's a bug in this file, not at evaluation time.

Scope for Step 8a: 8 starter KPIs across Procurement, Inventory, Sales,
and Finance. All are expressible with the fixed operation set.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.analytics import KPIDefinition

# (key, display_name, domain, unit, value_type, formula, description)
KPIS: list[tuple[str, str, str, str | None, str, dict, str | None]] = [
    (
        "procurement.total_spend",
        "Total Procurement Spend",
        "Procurement",
        "currency",
        "number",
        {"op": "sum", "concept": "Procurement.PurchasePrice"},
        "Total of all purchase prices across the tenant's procurement data.",
    ),
    (
        "procurement.spend_by_supplier",
        "Spend by Supplier",
        "Procurement",
        "currency",
        "number",
        {
            "op": "sum",
            "concept": "Procurement.PurchasePrice",
            "group_by": ["Procurement.Supplier"],
        },
        "Total purchase price grouped by supplier.",
    ),
    (
        "procurement.avg_lead_time",
        "Average Lead Time",
        "Procurement",
        "days",
        "number",
        {"op": "avg", "concept": "Procurement.LeadTime"},
        "Average lead time across all procurement lines.",
    ),
    (
        "inventory.total_stock",
        "Total Stock Level",
        "Inventory",
        "count",
        "number",
        {"op": "sum", "concept": "Inventory.StockLevel"},
        "Sum of stock levels across all SKUs.",
    ),
    (
        "inventory.stock_by_product",
        "Stock by Product",
        "Inventory",
        "count",
        "number",
        {
            "op": "sum",
            "concept": "Inventory.StockLevel",
            "group_by": ["Inventory.Product"],
        },
        "Total stock level grouped by product.",
    ),
    (
        "sales.total_revenue",
        "Total Revenue",
        "Sales",
        "currency",
        "number",
        {"op": "sum", "concept": "Finance.Revenue"},
        "Sum of all revenue-typed amounts in the tenant's data.",
    ),
    (
        "sales.revenue_by_customer",
        "Revenue by Customer",
        "Sales",
        "currency",
        "number",
        {
            "op": "sum",
            "concept": "Finance.Revenue",
            "group_by": ["Sales.Customer"],
        },
        "Total revenue grouped by customer.",
    ),
    (
        "finance.gross_margin_pct",
        "Gross Margin %",
        "Finance",
        "percent",
        "number",
        {
            "op": "ratio",
            "numerator": {
                "op": "difference",
                "left": {"op": "sum", "concept": "Finance.Revenue"},
                "right": {"op": "sum", "concept": "Finance.Cost"},
            },
            "denominator": {"op": "sum", "concept": "Finance.Revenue"},
            "scale": 100,
        },
        "Percentage of revenue remaining after direct costs.",
    ),
]


async def seed_kpis(db: AsyncSession) -> int:
    """
    Reconcile KPIDefinition rows to KPIS. Inserts and updates. Never
    deletes. Returns the number of definitions reconciled.
    """
    existing = {
        k.key: k
        for k in (await db.execute(select(KPIDefinition))).scalars().all()
    }

    reconciled = 0
    for key, display_name, domain, unit, value_type, formula, description in KPIS:
        if key in existing:
            k = existing[key]
            k.display_name = display_name
            k.domain = domain
            k.unit = unit
            k.value_type = value_type
            k.formula = formula
            k.description = description
        else:
            db.add(KPIDefinition(
                key=key,
                display_name=display_name,
                domain=domain,
                unit=unit,
                value_type=value_type,
                formula=formula,
                description=description,
            ))
        reconciled += 1

    await db.flush()
    return reconciled