# app/services/workflows/templates/management.py
from __future__ import annotations

from app.services.workflows.base import Step, WorkflowRequirement
from app.services.workflows.registry import register


class ExecutiveSummaryWorkflow:
    key = "management.executive_summary"
    display_name = "Executive Summary"
    description = (
        "Cross-functional KPI synthesis: runs the full KPI catalog and "
        "recent anomalies across all domains."
    )
    domain = "management"
    trigger_keywords = frozenset({
        "summary", "overview", "executive", "board", "kpi", "kpis",
        "state of", "how are we",
    })
    requirements: list[WorkflowRequirement] = []  # deliberately open: uses whatever's available
    steps = [
        Step(
            name="synthesize_kpis",
            type="analyze",
            params={
                "capability": "kpi",
                "capability_params": {},
                "store_as": "all_kpis",
            },
        ),
        Step(
            name="recent_anomalies",
            type="analyze",
            params={
                "capability": "anomaly",
                "capability_params": {},
                "store_as": "all_anomalies",
            },
        ),
        Step(
            name="recent_documents",
            type="analyze",
            params={
                "capability": "retrieval",
                "capability_params": {"top_k": 15},
                "store_as": "recent_context",
            },
        ),
    ]


register(ExecutiveSummaryWorkflow())