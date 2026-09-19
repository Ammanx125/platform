# app/db/seed_concepts.py
"""
Canonical concept catalog.

Global (not tenant-scoped). Idempotent: running seed_canonical_concepts()
repeatedly reconciles the catalog to this file.

~31 concepts across Procurement, Inventory, Finance, Sales, Operations,
Management. Two hard rules:

  1. No derived metrics. 'Gross Margin' is a KPIDefinition (Step 8),
     not a CanonicalConcept.
  2. No industry-specific concepts. Those go in Industry Packs (Step 24).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.semantic import CanonicalConcept

# Each entry: (key, display_name, domain, kind, value_type, synonyms, description)
CONCEPTS: list[tuple[str, str, str, str, str, list[str], str | None]] = [
    # ---------- Procurement (5) ----------
    (
        "Procurement.Supplier", "Supplier", "Procurement", "entity", "entity",
        ["supplier", "vendor", "supplier_name", "vendor_name", "supplier_id", "vendor_id"],
        "A party that provides goods or services to the organisation.",
    ),
    (
        "Procurement.Purchase", "Purchase", "Procurement", "fact", "number",
        ["purchase", "purchase_line", "po_line", "line_item"],
        "A single purchased line: quantity of a product at a price on a date.",
    ),
    (
        "Procurement.PurchaseOrder", "Purchase Order", "Procurement", "fact", "string",
        ["purchase_order", "po", "po_number", "po_id", "order_number"],
        "A formal order placed with a supplier.",
    ),
    (
        "Procurement.PurchasePrice", "Purchase Price", "Procurement", "attribute", "number",
        ["purchase_price", "buy_price", "cost_price", "unit_cost", "unit_price"],
        "The per-unit price paid to a supplier.",
    ),
    (
        "Procurement.LeadTime", "Lead Time", "Procurement", "attribute", "number",
        ["lead_time", "delivery_days", "lead_time_days"],
        "Days from order placement to delivery.",
    ),

    # ---------- Inventory (6) ----------
    (
        "Inventory.Product", "Product", "Inventory", "entity", "entity",
        ["product", "product_name", "item", "item_name", "material", "product_id", "item_id"],
        "A physical item that is bought, sold, or stocked.",
    ),
    (
        "Inventory.SKU", "SKU", "Inventory", "attribute", "string",
        ["sku", "sku_code", "product_code", "item_code", "part_number"],
        "A stock-keeping unit identifier for a product.",
    ),
    (
        "Inventory.StockLevel", "Stock Level", "Inventory", "attribute", "number",
        ["stock_level", "stock", "on_hand", "quantity_on_hand", "qty_on_hand", "inventory"],
        "The quantity of a product currently held.",
    ),
    (
        "Inventory.StockMovement", "Stock Movement", "Inventory", "fact", "number",
        ["stock_movement", "movement", "inventory_movement", "transfer"],
        "A change to a stock level: receipt, issue, or transfer.",
    ),
    (
        "Inventory.Warehouse", "Warehouse", "Inventory", "entity", "entity",
        ["warehouse", "warehouse_name", "location", "site", "warehouse_id"],
        "A physical location where stock is held.",
    ),
    (
        "Inventory.ReorderPoint", "Reorder Point", "Inventory", "attribute", "number",
        ["reorder_point", "reorder_level", "min_stock", "min_level", "safety_stock"],
        "The stock level at which a new order is triggered.",
    ),

    # ---------- Finance (6) ----------
    (
        "Finance.Revenue", "Revenue", "Finance", "attribute", "number",
        ["revenue", "sales_amount", "total_sales", "net_sales", "gross_sales", "invoice_total"],
        "Income from sales of goods or services.",
    ),
    (
        "Finance.Expense", "Expense", "Finance", "attribute", "number",
        ["expense", "expenses", "cost", "spend", "amount"],
        "An outflow of money to run the business.",
    ),
    (
        "Finance.Cost", "Cost", "Finance", "attribute", "number",
        ["cost", "total_cost", "direct_cost", "cogs", "cost_of_goods"],
        "Money spent to produce or acquire something.",
    ),
    (
        "Finance.Payment", "Payment", "Finance", "fact", "number",
        ["payment", "payment_amount", "paid", "payment_date"],
        "A movement of money in or out.",
    ),
    (
        "Finance.Invoice", "Invoice", "Finance", "fact", "string",
        ["invoice", "invoice_number", "invoice_id", "bill_number", "invoice_no"],
        "A request for payment for goods or services.",
    ),
    (
        "Finance.Currency", "Currency", "Finance", "attribute", "string",
        ["currency", "currency_code", "ccy"],
        "The unit of money in which amounts are denominated.",
    ),

    # ---------- Sales (5) ----------
    (
        "Sales.Customer", "Customer", "Sales", "entity", "entity",
        ["customer", "customer_name", "client", "client_name", "customer_id", "client_id", "cust_nm"],
        "A party that buys goods or services from the organisation.",
    ),
    (
        "Sales.Sale", "Sale", "Sales", "fact", "number",
        ["sale", "sales_line", "sales_order_line", "order_line"],
        "A single sold line: quantity of a product at a price on a date.",
    ),
    (
        "Sales.SalesOrder", "Sales Order", "Sales", "fact", "string",
        ["sales_order", "so", "so_number", "order_number", "order_id"],
        "A formal order received from a customer.",
    ),
    (
        "Sales.SellingPrice", "Selling Price", "Sales", "attribute", "number",
        ["selling_price", "sale_price", "price", "unit_price", "list_price"],
        "The per-unit price charged to a customer.",
    ),
    (
        "Sales.SalesChannel", "Sales Channel", "Sales", "attribute", "string",
        ["channel", "sales_channel", "source", "channel_name"],
        "The route through which a sale is made (web, retail, wholesale, ...).",
    ),

    # ---------- Operations (5) ----------
    (
        "Operations.Production", "Production", "Operations", "fact", "number",
        ["production", "production_run", "batch", "manufacturing_run"],
        "A manufacturing or assembly run producing goods.",
    ),
    (
        "Operations.ProductionCost", "Production Cost", "Operations", "attribute", "number",
        ["production_cost", "manufacturing_cost", "conversion_cost"],
        "The cost incurred to produce a quantity of goods.",
    ),
    (
        "Operations.Capacity", "Capacity", "Operations", "attribute", "number",
        ["capacity", "capacity_units", "max_capacity"],
        "The maximum output a line, site, or resource can produce in a period.",
    ),
    (
        "Operations.Downtime", "Downtime", "Operations", "attribute", "number",
        ["downtime", "downtime_minutes", "downtime_hours", "stop_time"],
        "Time during which a resource is unavailable.",
    ),
    (
        "Operations.Delivery", "Delivery", "Operations", "fact", "date",
        ["delivery", "delivery_date", "shipped_date", "received_date", "dispatch_date"],
        "A physical handover of goods to or from a party.",
    ),

    # ---------- Management (4) ----------
    (
        "Management.KPI", "KPI", "Management", "entity", "entity",
        ["kpi", "kpi_name", "metric", "metric_name", "indicator"],
        "A named metric tracked over time.",
    ),
    (
        "Management.Target", "Target", "Management", "attribute", "number",
        ["target", "target_value", "goal", "objective", "quota"],
        "A desired value for a metric in a period.",
    ),
    (
        "Management.Budget", "Budget", "Management", "attribute", "number",
        ["budget", "budget_amount", "planned_spend"],
        "An allocated amount for a cost category or period.",
    ),
    (
        "Management.BusinessUnit", "Business Unit", "Management", "entity", "entity",
        ["business_unit", "bu", "division", "department", "segment", "unit"],
        "An organisational subdivision used for reporting.",
    ),
]


# Common date/quantity/amount column names that don't warrant a dedicated
# entity concept but should still map to a typed attribute. These are the
# "generic" attributes most datasets carry.
GENERIC_ATTRIBUTES: list[tuple[str, str, str, str, str, list[str], str | None]] = [
    (
        "Common.Date", "Date", "Common", "attribute", "date",
        ["date", "transaction_date", "order_date", "invoice_date", "record_date", "created_date"],
        "A calendar date associated with a record.",
    ),
    (
        "Common.Quantity", "Quantity", "Common", "attribute", "number",
        ["quantity", "qty", "count", "units", "quantity_sold", "quantity_ordered"],
        "A count of items or units.",
    ),
    (
        "Common.Amount", "Amount", "Common", "attribute", "number",
        ["amount", "total", "value", "total_amount", "line_total", "sum"],
        "A monetary value.",
    ),
    (
        "Common.Name", "Name", "Common", "attribute", "string",
        ["name", "title", "label", "description"],
        "A human-readable label for a record.",
    ),
]


ALL_CONCEPTS = CONCEPTS + GENERIC_ATTRIBUTES


async def seed_canonical_concepts(db: AsyncSession) -> int:
    """
    Idempotent reconciliation of the global canonical concept catalog.

    - Inserts concepts that don't exist.
    - Updates display_name, description, synonyms, domain, kind, value_type
      on existing concepts, so edits to this file are picked up on re-run.
    - Does NOT delete concepts. Manual deletion is a deliberate operation.

    Returns the number of concepts reconciled (inserted or updated).
    """
    existing = {
        c.key: c
        for c in (await db.execute(select(CanonicalConcept))).scalars().all()
    }

    reconciled = 0
    for key, display_name, domain, kind, value_type, synonyms, description in ALL_CONCEPTS:
        if key in existing:
            c = existing[key]
            c.display_name = display_name
            c.domain = domain
            c.kind = kind
            c.value_type = value_type
            c.synonyms = synonyms
            c.description = description
        else:
            db.add(CanonicalConcept(
                key=key,
                display_name=display_name,
                domain=domain,
                kind=kind,
                value_type=value_type,
                synonyms=synonyms,
                description=description,
            ))
        reconciled += 1

    await db.flush()
    return reconciled