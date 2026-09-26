# app/services/workflows/templates/sales.py
from __future__ import annotations

from app.services.workflows.base import Step, WorkflowRequirement
from app.services.workflows.registry import register


class PipelineAnalysisWorkflow:
    key = "sales.pipeline_analysis"
    display_name = "Sales Pipeline Analysis"
    description = "Revenue trends, top customers, and customer concentration."
    domain = "sales"
    trigger_keywords = frozenset({
        "sales", "pipeline", "customer", "customers", "deal", "deals",
    })
    requirements = [
        WorkflowRequirement(kind="concept", key="Sales.Customer", optional=True),
        WorkflowRequirement(kind="kpi", key="sales.total_revenue", optional=True),
        WorkflowRequirement(kind="kpi", key="sales.revenue_by_customer", optional=True),
        WorkflowRequirement(kind="detector", key="sales.revenue_swings", optional=True),
    ]
    steps = [
        Step(
            name="analyze_sales",
            type="analyze",
            params={
                "capability": "kpi",
                "capability_params": {
                    "keys": [
                        "sales.total_revenue",
                        "sales.revenue_by_customer",
                    ],
                },
                "store_as": "sales_kpis",
            },
        ),
        Step(
            name="detect_revenue_swings",
            type="analyze",
            params={
                "capability": "anomaly",
                "capability_params": {"keys": ["sales.revenue_swings"]},
                "store_as": "revenue_anomalies",
            },
        ),
    ]


register(PipelineAnalysisWorkflow())