# app/services/workflows/templates/operations.py
from __future__ import annotations

from app.services.workflows.base import Step, WorkflowRequirement
from app.services.workflows.registry import register


class SLAMonitoringWorkflow:
    key = "operations.sla_monitoring"
    display_name = "Operations SLA Monitoring"
    description = "Downtime anomalies and capacity utilization."
    domain = "operations"
    trigger_keywords = frozenset({
        "operations", "downtime", "sla", "capacity", "bottleneck",
    })
    requirements = [
        WorkflowRequirement(kind="concept", key="Operations.Downtime", optional=True),
        WorkflowRequirement(kind="concept", key="Operations.Capacity", optional=True),
        WorkflowRequirement(kind="detector", key="operations.downtime_spikes", optional=True),
    ]
    steps = [
        Step(
            name="analyze_operations",
            type="analyze",
            params={
                "capability": "kpi",
                "capability_params": {},
                "store_as": "ops_kpis",
            },
        ),
        Step(
            name="detect_downtime_spikes",
            type="analyze",
            params={
                "capability": "anomaly",
                "capability_params": {"keys": ["operations.downtime_spikes"]},
                "store_as": "ops_anomalies",
            },
        ),
    ]


register(SLAMonitoringWorkflow())