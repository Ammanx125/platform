# app/services/orchestration/context.py
"""
Shared dataclasses for the orchestrator.

No DB, no LLM, no capabilities. This module defines the shapes that flow
between planner -> executor -> caller.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.services.llm.schemas import LLMResponse


@dataclass
class OrchestratorRequest:
    """
    The input to a decision.

    query is the user's question. Everything else is an optional hint that
    improves the planner's priors — it is not a command. The planner is
    free to ignore hints if the query contradicts them.
    """
    query: str
    domain_hint: str | None = None
    source_ids: list[uuid.UUID] | None = None


@dataclass
class PlanStep:
    """
    One capability invocation selected by the planner.

    reason explains *why* the planner chose this step — useful for the
    audit trail and for debugging the router.
    """
    capability: str
    reason: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class Plan:
    """
    The set of capability invocations for one request.

    routing_method: how the plan was produced — "rules" | "llm" | "all".
    The orchestrator prefers rules; falls back to LLM; falls back to
    running all capabilities if both fail.
    """
    steps: list[PlanStep]
    routing_method: str
    intent: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvidenceItem:
    """
    One piece of evidence shown to the LLM.

    kind:             "chunk" | "kpi" | "anomaly" | "forecast"
    id:               stable identifier the LLM can cite in claims[].evidence_ids
    text:             the evidence rendered as text for prose sections
    data:             the structured payload (for the JSON section)
    score:            optional relevance score
    injection_flags:  signal names from the injection scanner. Non-empty
                      means the content was flagged as suspicious. The
                      content is still delivered to the LLM, wrapped.
    """
    kind: str
    id: str
    text: str
    data: dict[str, Any]
    score: float | None = None
    injection_flags: list[str] = field(default_factory=list)

@dataclass
class Evidence:
    """
    The evidence package assembled from capability results.

    Categorized so the prompt can present each kind distinctly. Each list
    is capped by the executor.
    """
    chunks: list[EvidenceItem] = field(default_factory=list)
    kpis: list[EvidenceItem] = field(default_factory=list)
    anomalies: list[EvidenceItem] = field(default_factory=list)
    forecasts: list[EvidenceItem] = field(default_factory=list)
    workflows: list[EvidenceItem] = field(default_factory=list)
    capability_errors: list[dict[str, Any]] = field(default_factory=list)

    def all_items(self) -> list[EvidenceItem]:
        return [
            *self.chunks, *self.kpis,
            *self.anomalies, *self.forecasts, *self.workflows,
        ]

    def to_dict(self) -> dict[str, Any]:
        def _wrap(item: EvidenceItem) -> dict[str, Any]:
            d = dict(item.data)
            if item.injection_flags:
                d["_injection_flags"] = item.injection_flags
            return d
        return {
            "chunks": [_wrap(i) for i in self.chunks],
            "kpis": [_wrap(i) for i in self.kpis],
            "anomalies": [_wrap(i) for i in self.anomalies],
            "forecasts": [_wrap(i) for i in self.forecasts],
            "workflows": [_wrap(i) for i in self.workflows],
            "capability_errors": self.capability_errors,
        }

@dataclass
class ValidatedClaim:
    """A claim that cited valid evidence."""
    text: str
    evidence_ids: list[str]


@dataclass
class DecisionRunResult:
    """
    The output of one orchestrator execution.

    Persisted as a DecisionRun row. The executor returns this and the API
    layer reads the row back for the response.
    """
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    query: str

    plan: Plan
    evidence: Evidence
    llm_response: LLMResponse | None
    source_ids: list[uuid.UUID] = field(default_factory=list)
    validated_claims: list[ValidatedClaim] = field(default_factory=list)
    dropped_claims: list[dict[str, Any]] = field(default_factory=list)
    action_records: list[Any] = field(default_factory=list)

    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int = 0