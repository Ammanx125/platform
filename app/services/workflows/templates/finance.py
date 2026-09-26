# app/services/workflows/templates/finance.py
from __future__ import annotations

from app.services.workflows.base import Step, WorkflowRequirement
from app.services.workflows.registry import register


class CashFlowWorkflow:
    key = "finance.cash_flow"
    display_name = "Cash Flow Analysis"
    description = "Analyze revenue, cost, and margin KPIs."
    domain = "finance"
    trigger_keywords = frozenset({
        "cash", "flow", "margin", "revenue", "cost", "finance",
    })
    requirements = [
        WorkflowRequirement(kind="concept", key="Finance.Revenue", optional=True),
        WorkflowRequirement(kind="concept", key="Finance.Cost", optional=True),
        WorkflowRequirement(kind="kpi", key="finance.gross_margin_pct", optional=True),
        WorkflowRequirement(kind="kpi", key="sales.total_revenue", optional=True),
    ]
    steps = [
        Step(
            name="analyze_finance",
            type="analyze",
            params={
                "capability": "kpi",
                "capability_params": {
                    "keys": [
                        "finance.gross_margin_pct",
                        "sales.total_revenue",
                    ],
                },
                "store_as": "finance_kpis",
            },
        ),
    ]


register(CashFlowWorkflow())