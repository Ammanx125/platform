# app/db/seed_concepts.py
"""
Canonical concept catalog.

Global (not tenant-scoped). Idempotent: running seed_canonical_concepts()
repeatedly reconciles the catalog to this file.

Two hard rules:
  1. No derived metrics. 'Gross Margin' is a KPIDefinition (Step 8).
  2. No industry-specific concepts. Those go in Industry Packs (Step 24).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.semantic import CanonicalConcept

# (key, display_name, domain, kind, value_type, synonyms, description, unit, currency)
#
# unit labels the measure of a concept's values ('currency', 'count', 'days',
# ...). Currency is intentionally not set in this global catalog: it varies
# by tenant and source.

CONCEPTS: list[
    tuple[str, str, str, str, str, list[str], str | None, str | None, str | None]
] = [
    # ---------- Procurement (5) ----------
    (
        "Procurement.Supplier", "Supplier", "Procurement", "entity", "entity",
        ["supplier", "vendor", "supplier_name", "vendor_name", "supplier_id", "vendor_id"],
        "A party that provides goods or services to the organisation.",
        None, None,
    ),
    (
        "Procurement.Purchase", "Purchase", "Procurement", "fact", "number",
        ["purchase", "purchase_line", "po_line", "line_item"],
        "A single purchased line: quantity of a product at a price on a date.",
        None, None,
    ),
    (
        "Procurement.PurchaseOrder", "Purchase Order", "Procurement", "fact", "string",
        ["purchase_order", "po", "po_number", "po_id", "order_number"],
        "A formal order placed with a supplier.",
        None, None,
    ),
    (
        "Procurement.PurchasePrice", "Purchase Price", "Procurement", "attribute", "number",
        ["purchase_price", "buy_price", "cost_price", "unit_cost", "unit_price"],
        "Per-unit price paid to a supplier for a purchased item.",
        "currency", None,
    ),
    (
        "Procurement.LeadTime", "Lead Time", "Procurement", "attribute", "number",
        ["lead_time", "delivery_days", "lead_time_days"],
        "Days from order placement to delivery.",
        "days", None,
    ),

    # ---------- Inventory (6) ----------
    (
        "Inventory.Product", "Product", "Inventory", "entity", "entity",
        ["product", "product_name", "item", "item_name", "material", "product_id", "item_id"],
        "A physical item that is bought, sold, or stocked.",
        None, None,
    ),
    (
        "Inventory.SKU", "SKU", "Inventory", "attribute", "string",
        ["sku", "sku_code", "product_code", "item_code", "part_number"],
        "A stock-keeping unit identifier for a product.",
        None, None,
    ),
    (
        "Inventory.StockLevel", "Stock Level", "Inventory", "attribute", "number",
        ["stock_level", "stock", "on_hand", "quantity_on_hand", "qty_on_hand", "inventory"],
        "The quantity of a product currently held.",
        "count", None,
    ),
    (
        "Inventory.StockMovement", "Stock Movement", "Inventory", "fact", "number",
        ["stock_movement", "movement", "inventory_movement", "transfer"],
        "A change to a stock level: receipt, issue, or transfer.",
        None, None,
    ),
    (
        "Inventory.Warehouse", "Warehouse", "Inventory", "entity", "entity",
        ["warehouse", "warehouse_name", "location", "site", "warehouse_id"],
        "A physical location where stock is held.",
        None, None,
    ),
    (
        "Inventory.ReorderPoint", "Reorder Point", "Inventory", "attribute", "number",
        ["reorder_point", "reorder_level", "min_stock", "min_level", "safety_stock"],
        "The stock level at which a new order is triggered.",
        "count", None,
    ),

    # ---------- Finance (7) ----------
    (
        "Finance.Revenue", "Revenue", "Finance", "attribute", "number",
        [
            "revenue", "sales", "sale", "sales_amount", "total_sales",
            "net_sales", "gross_sales", "invoice_total",
        ],
        "Income from sales of goods or services.",
        "currency", None,
    ),
    (
        "Finance.Expense", "Expense", "Finance", "attribute", "number",
        ["expense", "expenses", "spend"],
        "An outflow of money to run the business.",
        "currency", None,
    ),
    (
        "Finance.Cost", "Cost", "Finance", "attribute", "number",
        ["cost", "total_cost", "direct_cost", "operating_cost", "operating_expense"],
        "A general cost amount; not necessarily cost of goods sold.",
        "currency", None,
    ),
    (
        "Finance.CostOfGoodsSold", "Cost of Goods Sold", "Finance", "attribute", "number",
        ["cogs", "cost_of_goods_sold", "cost_of_goods"],
        "Direct cost attributable to goods or services sold in the same reporting baseline.",
        "currency", None,
    ),
    (
        "Finance.Payment", "Payment", "Finance", "fact", "number",
        ["payment", "payment_amount", "paid", "payment_date"],
        "A movement of money in or out.",
        "currency", None,
    ),
    (
        "Finance.Invoice", "Invoice", "Finance", "fact", "string",
        ["invoice", "invoice_number", "invoice_id", "bill_number", "invoice_no"],
        "A request for payment for goods or services.",
        None, None,
    ),
    (
        "Finance.Currency", "Currency", "Finance", "attribute", "string",
        ["currency", "currency_code", "ccy"],
        "The unit of money in which amounts are denominated.",
        None, None,
    ),

    # ---------- Sales (5) ----------
    (
        "Sales.Customer", "Customer", "Sales", "entity", "entity",
        [
            "customer", "customer_name", "client", "client_name",
            "customer_id", "client_id", "cust_nm",
        ],
        "A party that buys goods or services from the organisation.",
        None, None,
    ),
    (
        "Sales.Sale", "Sale", "Sales", "fact", "number",
        ["sale", "sales_line", "sales_order_line", "order_line"],
        "A single sold line: quantity of a product at a price on a date.",
        None, None,
    ),
    (
        "Sales.SalesOrder", "Sales Order", "Sales", "fact", "string",
        ["sales_order", "so", "so_number", "order_number", "order_id"],
        "A formal order received from a customer.",
        None, None,
    ),
    (
        "Sales.TransactionId", "Sales Transaction ID", "Sales", "attribute", "string",
        [
            "transaction_id", "transaction_number", "transaction_ref",
            "sales_transaction_id", "receipt_id", "ticket_ref",
        ],
        "A stable identifier for one customer transaction or purchase.",
        None, None,
    ),
    (
        "Sales.TransactionDate", "Sales Transaction Date", "Sales", "attribute", "date",
        [
            "sales_date", "purchase_date", "sale_date", "transaction_day",
        ],
        "The date on which a customer transaction occurred.",
        None, None,
    ),
    (
        "Sales.SellingPrice", "Selling Price", "Sales", "attribute", "number",
        ["selling_price", "sale_price", "price", "unit_price", "list_price"],
        "The per-unit price charged to a customer.",
        "currency", None,
    ),
    (
        "Sales.SalesChannel", "Sales Channel", "Sales", "attribute", "string",
        ["channel", "sales_channel", "source", "channel_name"],
        "The route through which a sale is made (web, retail, wholesale, ...).",
        None, None,
    ),

    # ---------- Operations (5) ----------
    (
        "Operations.Production", "Production", "Operations", "fact", "number",
        ["production", "production_run", "batch", "manufacturing_run"],
        "A manufacturing or assembly run producing goods.",
        "count", None,
    ),
    (
        "Operations.ProductionCost", "Production Cost", "Operations", "attribute", "number",
        ["production_cost", "manufacturing_cost", "conversion_cost"],
        "The cost incurred to produce a quantity of goods.",
        "currency", None,
    ),
    (
        "Operations.Capacity", "Capacity", "Operations", "attribute", "number",
        ["capacity", "capacity_units", "max_capacity"],
        "The maximum output a line, site, or resource can produce in a period.",
        "count", None,
    ),
    (
        "Operations.Downtime", "Downtime", "Operations", "attribute", "number",
        ["downtime", "downtime_minutes", "downtime_hours", "stop_time"],
        "Time during which a resource is unavailable.",
        "hours", None,
    ),
    (
        "Operations.Delivery", "Delivery", "Operations", "fact", "date",
        ["delivery", "delivery_date", "shipped_date", "received_date", "dispatch_date"],
        "A physical handover of goods to or from a party.",
        None, None,
    ),

    # ---------- Management (4) ----------
    (
        "Management.KPI", "KPI", "Management", "entity", "entity",
        ["kpi", "kpi_name", "metric", "metric_name", "indicator"],
        "A named metric tracked over time.",
        None, None,
    ),
    (
        "Management.Target", "Target", "Management", "attribute", "number",
        ["target", "target_value", "goal", "objective", "quota"],
        "A desired value for a metric in a period.",
        None, None,  # inherits the measure of the metric it targets
    ),
    (
        "Management.Budget", "Budget", "Management", "attribute", "number",
        ["budget", "budget_amount", "planned_spend"],
        "An allocated amount for a cost category or period.",
        "currency", None,
    ),
    (
        "Management.BusinessUnit", "Business Unit", "Management", "entity", "entity",
        [
            "business_unit", "bu", "division", "department", "segment", "unit",
            "depot", "depot_name", "depot_id",
        ],
        "An organisational subdivision used for reporting.",
        None, None,
    ),
]


# Generic attributes most datasets carry. Not domain concepts, but needed as
# mapping targets so common columns like 'date' or 'amount' have a home.
GENERIC_ATTRIBUTES: list[
    tuple[str, str, str, str, str, list[str], str | None, str | None, str | None]
] = [
    (
        "Common.Date", "Date", "Common", "attribute", "date",
        [
            "date", "transaction_date", "order_date", "invoice_date",
            "record_date", "created_date",
        ],
        "A calendar date associated with a record.",
        None, None,
    ),
    (
        "Common.Quantity", "Quantity", "Common", "attribute", "number",
        ["quantity", "qty", "count", "units", "quantity_sold", "quantity_ordered"],
        "A count of items or units.",
        "count", None,
    ),
    (
        "Common.Amount", "Amount", "Common", "attribute", "number",
        ["amount", "total", "value", "total_amount", "line_total", "sum"],
        "A monetary value.",
        "currency", None,
    ),
    (
        "Common.Name", "Name", "Common", "attribute", "string",
        ["name", "title", "label", "description"],
        "A human-readable label for a record.",
        None, None,
    ),
]


ALL_CONCEPTS = CONCEPTS + GENERIC_ATTRIBUTES

# Units a seeded concept may declare. Codes are stored verbatim and are
# rendered by the dashboard; they are not converted anywhere.
KNOWN_UNITS = frozenset({
    "currency",
    "count",
    "days",
    "hours",
    "minutes",
    "percentage",
    "litres",
    "litres_per_100km",
    "kilometres",
})


def _validate_metadata() -> None:
    """
    Fail the seed loudly rather than persist a half-labelled concept.

    Rules:
      - every concept has a non-empty display_name
      - unit, when set, is a known unit
      - currency may only be set when unit == 'currency'
    """
    for concept in ALL_CONCEPTS:
        key, display_name, _domain, _kind, _value_type, _syn, _desc, unit, currency = concept
        if not display_name:
            raise ValueError(f"concept {key!r} has no display_name")
        if unit is not None and unit not in KNOWN_UNITS:
            raise ValueError(f"concept {key!r} declares unknown unit {unit!r}")
        if unit != "currency" and currency:
            raise ValueError(
                f"concept {key!r} declares currency {currency!r} "
                f"but unit is {unit!r}"
            )


_validate_metadata()


async def seed_canonical_concepts(db: AsyncSession) -> int:
    """
    Idempotent reconciliation of the global canonical concept catalog.

    Inserts concepts that don't exist and updates mutable fields on existing
    ones (so edits to this file are picked up on re-run). Never deletes.

    Returns the number of concepts reconciled (inserted or updated).
    """
    existing: dict[str, CanonicalConcept] = {
        c.key: c
        for c in (await db.execute(select(CanonicalConcept))).scalars().all()
    }

    reconciled = 0
    for (
        key, display_name, domain, kind, value_type, synonyms, description,
        unit, currency,
    ) in ALL_CONCEPTS:
        if key in existing:
            c = existing[key]
            c.display_name = display_name
            c.domain = domain
            c.kind = kind
            c.value_type = value_type
            c.synonyms = synonyms
            c.description = description
            c.unit = unit
            c.currency = currency
        else:
            db.add(CanonicalConcept(
                key=key,
                display_name=display_name,
                domain=domain,
                kind=kind,
                value_type=value_type,
                synonyms=synonyms,
                description=description,
                unit=unit,
                currency=currency,
            ))
        reconciled += 1

    await db.flush()
    return reconciled