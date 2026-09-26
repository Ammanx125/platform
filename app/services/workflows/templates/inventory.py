# app/services/workflows/templates/inventory.py
from __future__ import annotations

from app.services.workflows.base import Step, WorkflowRequirement
from app.services.workflows.registry import register


class StockAnalysisWorkflow:
    key = "inventory.stock_analysis"
    display_name = "Inventory Stock Analysis"
    description = "Analyze stock levels, grouped by product, and flag anomalies."
    domain = "inventory"
    trigger_keywords = frozenset({
        "inventory", "stock", "stockout", "warehouse", "sku",
    })
    requirements = [
        WorkflowRequirement(kind="concept", key="Inventory.StockLevel", optional=False),
        WorkflowRequirement(kind="kpi", key="inventory.total_stock", optional=True),
        WorkflowRequirement(kind="kpi", key="inventory.stock_by_product", optional=True),
        WorkflowRequirement(kind="detector", key="inventory.stock_level_drops", optional=True),
    ]
    steps = [
        Step(
            name="analyze_stock",
            type="analyze",
            params={
                "capability": "kpi",
                "capability_params": {
                    "keys": [
                        "inventory.total_stock",
                        "inventory.stock_by_product",
                    ],
                },
                "store_as": "stock_kpis",
            },
        ),
        Step(
            name="detect_stock_drops",
            type="analyze",
            params={
                "capability": "anomaly",
                "capability_params": {"keys": ["inventory.stock_level_drops"]},
                "store_as": "stock_anomalies",
            },
        ),
    ]


register(StockAnalysisWorkflow())