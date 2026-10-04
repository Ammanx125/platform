# app/db/seed_packs.py
"""
Starter industry packs.

Each pack is a self-contained bundle of concepts + relationships. Installing
it materializes those definitions into the global catalog.

Starter packs:
  - procurement.v1 — Supplier, Purchase, PurchaseOrder, PurchasePrice, LeadTime
  - inventory.v1   — Product, SKU, StockLevel, StockMovement, Warehouse, ReorderPoint
  - transport.v1   — Fleet, routes, passengers, fuel, and service-time concepts

Concepts and relationships are defined inline here (as Python data), but the
seed function persists them into IndustryPack rows so packs can be listed,
versioned, and installed through the API.

When you add a new pack, add it to PACKS and re-run scripts/seed_packs.
Existing installs are unaffected; the pack definition updates in place.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.semantic import IndustryPack

PACKS: list[dict] = [
    {
        "key": "procurement.v1",
        "display_name": "Procurement",
        "description": (
            "Supplier and purchase management. Covers purchase orders, "
            "supplier pricing, and lead-time tracking."
        ),
        "version": "1.0.0",
        "concepts": [
            # Concept definitions reference keys that already exist in the
            # global catalog (seeded by seed_concepts.py). A pack can also
            # introduce NEW concepts — those get inserted on install.
            {"key": "Procurement.Supplier"},
            {"key": "Procurement.Purchase"},
            {"key": "Procurement.PurchaseOrder"},
            {"key": "Procurement.PurchasePrice"},
            {"key": "Procurement.LeadTime"},
            {
                "key": "Procurement.PurchaseAmount",
                "display_name": "Purchase Amount",
                "domain": "Procurement",
                "kind": "attribute",
                "value_type": "number",
                "synonyms": [
                    "purchase_amount", "total_purchase", "order_total",
                    "procurement_spend", "purchase_total",
                ],
                "description": "The total monetary amount of a customer or supplier purchase.",
            },
            # A pack-only concept: not in the base catalog.
            {
                "key": "Procurement.RFQ",
                "display_name": "Request for Quotation",
                "domain": "Procurement",
                "kind": "fact",
                "value_type": "string",
                "synonyms": ["rfq", "request_for_quotation", "tender", "bid_request"],
                "description": "A formal request sent to suppliers for pricing.",
            },
        ],
        "relationships": [
            {
                "from_key": "Procurement.Purchase",
                "to_key": "Procurement.Supplier",
                "kind": "belongs_to",
                "cardinality": "N:1",
                "join_hint": {"left_column": "supplier_id", "right_column": "id"},
                "description": "A purchase is made from one supplier.",
            },
            {
                "from_key": "Procurement.Supplier",
                "to_key": "Procurement.Purchase",
                "kind": "has_many",
                "cardinality": "1:N",
                "description": "A supplier can have many purchases.",
            },
            {
                "from_key": "Procurement.Purchase",
                "to_key": "Procurement.PurchaseOrder",
                "kind": "belongs_to",
                "cardinality": "N:1",
                "join_hint": {"left_column": "po_id", "right_column": "id"},
                "description": "A purchase line belongs to a purchase order.",
            },
            {
                "from_key": "Procurement.PurchaseOrder",
                "to_key": "Procurement.Purchase",
                "kind": "has_many",
                "cardinality": "1:N",
                "description": "A purchase order has many purchase lines.",
            },
            {
                "from_key": "Procurement.Purchase",
                "to_key": "Inventory.Product",
                "kind": "references",
                "cardinality": "N:1",
                "join_hint": {"left_column": "product_id", "right_column": "id"},
                "description": "A purchase refers to a product.",
            },
            {
                "from_key": "Procurement.PurchasePrice",
                "to_key": "Procurement.Supplier",
                "kind": "references",
                "cardinality": "N:1",
                "description": "A price is quoted by a supplier.",
            },
            {
                "from_key": "Procurement.PurchaseOrder",
                "to_key": "Procurement.RFQ",
                "kind": "references",
                "cardinality": "N:1",
                "description": "A purchase order may originate from an RFQ.",
            },
        ],
        "kpi_stubs": [],   # populated in Step 8
    },
    {
        "key": "inventory.v1",
        "display_name": "Inventory",
        "description": (
            "Product and stock management. Covers SKUs, stock levels, "
            "warehouse locations, and reorder points."
        ),
        "version": "1.0.0",
        "concepts": [
            {"key": "Inventory.Product"},
            {"key": "Inventory.SKU"},
            {"key": "Inventory.StockLevel"},
            {"key": "Inventory.StockMovement"},
            {"key": "Inventory.Warehouse"},
            {"key": "Inventory.ReorderPoint"},
        ],
        "relationships": [
            {
                "from_key": "Inventory.SKU",
                "to_key": "Inventory.Product",
                "kind": "belongs_to",
                "cardinality": "N:1",
                "join_hint": {"left_column": "product_id", "right_column": "id"},
                "description": "An SKU identifies one product.",
            },
            {
                "from_key": "Inventory.Product",
                "to_key": "Inventory.SKU",
                "kind": "has_many",
                "cardinality": "1:N",
                "description": "A product can have multiple SKUs (variants, packs).",
            },
            {
                "from_key": "Inventory.StockLevel",
                "to_key": "Inventory.Product",
                "kind": "belongs_to",
                "cardinality": "N:1",
                "description": "A stock level is held for one product.",
            },
            {
                "from_key": "Inventory.StockLevel",
                "to_key": "Inventory.Warehouse",
                "kind": "belongs_to",
                "cardinality": "N:1",
                "join_hint": {"left_column": "warehouse_id", "right_column": "id"},
                "description": "A stock level is scoped to one warehouse.",
            },
            {
                "from_key": "Inventory.StockMovement",
                "to_key": "Inventory.Product",
                "kind": "references",
                "cardinality": "N:1",
                "description": "A stock movement changes one product's level.",
            },
            {
                "from_key": "Inventory.StockMovement",
                "to_key": "Inventory.Warehouse",
                "kind": "references",
                "cardinality": "N:1",
                "description": "A stock movement happens in one warehouse.",
            },
            {
                "from_key": "Inventory.ReorderPoint",
                "to_key": "Inventory.Product",
                "kind": "belongs_to",
                "cardinality": "N:1",
                "description": "A reorder point is configured per product.",
            },
            {
                "from_key": "Inventory.ReorderPoint",
                "to_key": "Inventory.Warehouse",
                "kind": "belongs_to",
                "cardinality": "N:1",
                "description": "A reorder point is per warehouse (or global if null).",
            },
        ],
        "kpi_stubs": [],
    },
    {
        "key": "transport.v1",
        "display_name": "Transport Operations",
        "description": (
            "Fleet, route, passenger, fuel-efficiency, and service-time "
            "concepts for transport operators."
        ),
        "version": "1.0.0",
        "concepts": [
            {
                "key": "Operations.OccupiedHours",
                "display_name": "Occupied Hours",
                "domain": "Operations",
                "kind": "attribute",
                "value_type": "number",
                "synonyms": ["occupied_hours", "active_hours", "hours_in_service"],
                "description": "Hours of scheduled capacity actually used.",
            },
            {
                "key": "Transport.Vehicle",
                "display_name": "Vehicle",
                "domain": "Transport",
                "kind": "entity",
                "value_type": "entity",
                "synonyms": ["vehicle", "vehicle_id", "vehicle_ref", "bus", "bus_id"],
                "description": "A vehicle used to provide transport services.",
            },
            {
                "key": "Transport.Route",
                "display_name": "Route",
                "domain": "Transport",
                "kind": "entity",
                "value_type": "entity",
                "synonyms": ["route", "route_name", "route_code", "service_route"],
                "description": "A defined path or service operated by the fleet.",
            },
            {
                "key": "Transport.PassengerCount",
                "display_name": "Passenger Count",
                "domain": "Transport",
                "kind": "attribute",
                "value_type": "number",
                "synonyms": ["passengers", "passenger_count", "riders", "rider_count"],
                "description": "The number of passengers carried.",
            },
            {
                "key": "Transport.DistanceKm",
                "display_name": "Distance KM",
                "domain": "Transport",
                "kind": "attribute",
                "value_type": "number",
                "synonyms": ["distance_km", "distance", "kilometres", "kilometers"],
                "description": "Distance travelled, measured in kilometres.",
            },
            {
                "key": "Transport.FuelConsumed",
                "display_name": "Fuel Consumed",
                "domain": "Transport",
                "kind": "attribute",
                "value_type": "number",
                "synonyms": ["fuel_litres", "fuel_liters", "litres_used", "liters_used"],
                "description": "Fuel consumed by a vehicle over a reporting period.",
            },
            {
                "key": "Transport.FuelUseRate",
                "display_name": "Fuel Use Rate",
                "domain": "Transport",
                "kind": "attribute",
                "value_type": "number",
                "synonyms": ["fuel_use_rate", "litres_per_100km", "liters_per_100km"],
                "description": "Fuel consumed per 100 kilometres.",
            },
            {
                "key": "Transport.TravelMinutes",
                "display_name": "Travel Minutes",
                "domain": "Transport",
                "kind": "attribute",
                "value_type": "number",
                "synonyms": ["travel_minutes", "journey_minutes", "trip_duration_minutes"],
                "description": "Elapsed travel time for a route service.",
            },
        ],
        "relationships": [
            {
                "from_key": "Transport.Vehicle",
                "to_key": "Management.BusinessUnit",
                "kind": "belongs_to",
                "cardinality": "N:1",
                "description": "Vehicles are assigned to an operating depot.",
            },
            {
                "from_key": "Transport.Route",
                "to_key": "Transport.Vehicle",
                "kind": "references",
                "cardinality": "N:M",
                "description": "Routes are serviced by fleet vehicles.",
            },
            {
                "from_key": "Transport.PassengerCount",
                "to_key": "Transport.Route",
                "kind": "references",
                "cardinality": "N:1",
                "description": "Passenger counts are reported for a route.",
            },
            {
                "from_key": "Transport.FuelConsumed",
                "to_key": "Transport.Vehicle",
                "kind": "references",
                "cardinality": "N:1",
                "description": "Fuel consumption is recorded for a vehicle.",
            },
        ],
        "kpi_stubs": [
            {
                "key": "transport.passenger_volume",
                "display_name": "Passenger Volume",
                "domain": "Transport",
                "unit": "count",
                "value_type": "number",
                "formula": {"op": "sum", "concept": "Transport.PassengerCount"},
                "description": "Passengers carried in the tenant's transport data.",
            },
            {
                "key": "transport.average_fuel_use_rate",
                "display_name": "Average Fuel Use Rate",
                "domain": "Transport",
                "unit": "litres_per_100km",
                "value_type": "number",
                "formula": {"op": "avg", "concept": "Transport.FuelUseRate"},
                "description": "Average litres consumed per 100 kilometres.",
            },
            {
                "key": "transport.average_travel_time",
                "display_name": "Average Route Travel Time",
                "domain": "Transport",
                "unit": "minutes",
                "value_type": "number",
                "formula": {"op": "avg", "concept": "Transport.TravelMinutes"},
                "description": "Average reported route travel time.",
            },
            {
                "key": "operations.capacity_utilization_pct",
                "display_name": "Capacity Utilization",
                "domain": "Operations",
                "unit": "percent",
                "value_type": "number",
                "formula": {
                    "op": "ratio",
                    "numerator": {
                        "op": "sum",
                        "concept": "Operations.OccupiedHours",
                    },
                    "denominator": {
                        "op": "sum",
                        "concept": "Operations.Capacity",
                    },
                    "scale": 100,
                },
                "description": (
                    "Occupied operating hours as a share of available capacity."
                ),
            },
            {
                "key": "transport.operating_cost",
                "display_name": "Transport Operating Cost",
                "domain": "Transport",
                "unit": "currency",
                "value_type": "number",
                "formula": {"op": "sum", "concept": "Finance.Cost"},
                "description": "Recorded transport operating costs.",
            },
        ],
    },
]


async def seed_industry_packs(db: AsyncSession) -> int:
    """
    Reconcile the IndustryPack rows to PACKS.

    Inserts new packs, updates existing ones. Never deletes. Returns the
    number of packs reconciled.

    Does NOT install packs for any tenant — that's the caller's job via
    install_pack().
    """
    existing = {
        p.key: p
        for p in (await db.execute(select(IndustryPack))).scalars().all()
    }

    reconciled = 0
    for spec in PACKS:
        if spec["key"] in existing:
            p = existing[spec["key"]]
            p.display_name = spec["display_name"]
            p.description = spec["description"]
            p.version = spec["version"]
            p.concepts = spec["concepts"]
            p.relationships = spec["relationships"]
            p.kpi_stubs = spec["kpi_stubs"]
        else:
            db.add(IndustryPack(
                key=spec["key"],
                display_name=spec["display_name"],
                description=spec["description"],
                version=spec["version"],
                concepts=spec["concepts"],
                relationships=spec["relationships"],
                kpi_stubs=spec["kpi_stubs"],
            ))
        reconciled += 1

    await db.flush()
    return reconciled