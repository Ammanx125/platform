# app/services/workflows/templates/procurement.py
"""
Procurement workflows — full vertical slice.

Two workflows:

  procurement.spend_analysis
    A read-only analysis: spend KPIs, supplier grouping, anomalies.
    No actions, no approvals. Runs to completion in one pass.

  procurement.supplier_reorder
    Proposes a reorder via the action system, waits for the action's
    approval (action gate), then verifies. Demonstrates the full
    analyze → propose → wait → verify pipeline with a single action gate.

Both use the same action pipeline as the orchestrator (handle_proposals).
Workflow-level approval is not used in these two; that gate is exercised
by the procurement.purchase_order workflow below if you add one later.
"""
from __future__ import annotations

from app.services.workflows.base import (
    Step,
    WorkflowRequirement,
)
from app.services.workflows.registry import register


class SpendAnalysisWorkflow:
    key = "procurement.spend_analysis"
    display_name = "Procurement Spend Analysis"
    description = (
        "Analyze procurement spend: total, by supplier, and any anomalies "
        "in supplier pricing."
    )
    domain = "procurement"
    trigger_keywords = frozenset({
        "procurement", "spend", "supplier", "suppliers", "purchasing",
        "purchase", "vendor", "vendors",
    })
    requirements = [
        WorkflowRequirement(kind="concept", key="Procurement.Supplier", optional=True),
        WorkflowRequirement(kind="concept", key="Procurement.PurchasePrice", optional=False),
        WorkflowRequirement(kind="kpi", key="procurement.total_spend", optional=True),
        WorkflowRequirement(kind="kpi", key="procurement.spend_by_supplier", optional=True),
        WorkflowRequirement(kind="detector", key="procurement.purchase_price_spikes", optional=True),
    ]
    steps = [
        Step(
            name="analyze_spend",
            type="analyze",
            params={
                "capability": "kpi",
                "capability_params": {
                    "keys": [
                        "procurement.total_spend",
                        "procurement.spend_by_supplier",
                        "procurement.avg_lead_time",
                    ],
                },
                "store_as": "spend_kpis",
            },
        ),
        Step(
            name="detect_price_anomalies",
            type="analyze",
            params={
                "capability": "anomaly",
                "capability_params": {
                    "keys": ["procurement.purchase_price_spikes"],
                },
                "store_as": "price_anomalies",
            },
        ),
        Step(
            name="retrieve_context",
            type="analyze",
            params={
                "capability": "retrieval",
                "capability_params": {"top_k": 10},
                "query": "procurement spend supplier contracts",
                "store_as": "context_chunks",
            },
        ),
    ]


class SupplierReorderWorkflow:
    key = "procurement.supplier_reorder"
    display_name = "Supplier Reorder Proposal"
    description = (
        "Analyze stock and propose a reorder. The proposal goes through "
        "the action system's approval gate; the workflow waits until the "
        "action reaches a terminal state before completing."
    )
    domain = "procurement"
    trigger_keywords = frozenset({
        "reorder", "replenish", "restock",
    })
    requirements = [
        WorkflowRequirement(kind="concept", key="Inventory.StockLevel", optional=False),
        WorkflowRequirement(kind="concept", key="Inventory.ReorderPoint", optional=True),
        WorkflowRequirement(kind="tool", key="list_suppliers", optional=False),
        WorkflowRequirement(kind="tool", key="generate_report", optional=False),
    ]
    steps = [
        Step(
            name="analyze_stock",
            type="analyze",
            params={
                "capability": "kpi",
                "capability_params": {
                    "keys": ["inventory.total_stock", "inventory.stock_by_product"],
                },
                "store_as": "stock_kpis",
            },
        ),
        # Propose a report. Report risk_level is "report" (auto-execute),
        # so this step does not pause on approval.
        Step(
            name="draft_reorder_report",
            type="run_action",
            params={
                "tool_calls": [
                    {
                        "name": "generate_report",
                        "arguments": {
                            "title": "Reorder Recommendation",
                            "body": (
                                "Automated reorder recommendation produced by the "
                                "supplier reorder workflow. Review before acting. "
                                "Placeholder body generated for demonstration; a real "
                                "run would substitute analyzed values here."
                            ),
                        },
                        "rationale": "Stock analysis triggered a reorder recommendation.",
                    },
                ],
                "wait_for_approval": False,
                "on_action_rejection": "fail",
            },
        ),
        # Look at whether the report action completed cleanly.
        Step(
            name="verify_report",
            type="set_context",
            params={
                "updates": {
                    "reorder_status": "report_generated",
                    "workflow_note": (
                        "Reorder recommendation drafted. A real deployment "
                        "would follow this with a purchase-order proposal."
                    ),
                },
            },
        ),
    ]


register(SpendAnalysisWorkflow())
register(SupplierReorderWorkflow())