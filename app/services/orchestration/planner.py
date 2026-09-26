# app/services/orchestration/planner.py
"""
Planner: decides which capabilities to invoke for a request.

Three tiers, in order of preference:

  1. Deterministic rules — fast, free, predictable. Cover the common cases.
  2. LLM routing — the long tail. One LLM call with a routing prompt.
  3. Run-all fallback — if both fail, run every capability.

The planner never executes anything. It returns a Plan. The executor
performs the invocations.
"""
from __future__ import annotations

import json
import re

from app.services.llm.base import LLMProviderError
from app.services.llm.registry import get_llm_provider
from app.services.orchestration.context import (
    OrchestratorRequest,
    Plan,
    PlanStep,
)
from app.services.workflows.registry import match_by_query as match_workflows

# Tokens that suggest a specific capability. Deliberately small; the LLM
# router handles the rest.

_WHY_TOKENS = frozenset({
    "why", "explain", "reason", "because", "cause", "caused",
})
_FORECAST_TOKENS = frozenset({
    "forecast", "predict", "projection", "next month", "next quarter",
    "will", "expect", "trend",
})
_ANOMALY_TOKENS = frozenset({
    "anomaly", "anomalies", "unusual", "spike", "outlier", "weird",
    "unexpected", "abnormal",
})
_TREND_TOKENS = frozenset({
    "increase", "decrease", "up", "down", "rise", "fell", "drop", "grew",
    "declined", "changed",
})
_METRIC_TOKENS = frozenset({
    "spend", "revenue", "cost", "margin", "stock", "inventory",
    "lead time", "downtime", "kpi", "metric",
})

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def _rules_plan(request: OrchestratorRequest) -> Plan | None:
    """
    Attempt to build a plan using deterministic rules. Returns None if no
    rule matches with sufficient confidence — the caller then tries LLM
    routing.
    """
    toks = _tokens(request.query)
    steps: list[PlanStep] = []

    wants_why = bool(toks & _WHY_TOKENS)
    wants_forecast = bool(toks & _FORECAST_TOKENS)
    wants_anomaly = bool(toks & _ANOMALY_TOKENS)
    wants_trend = bool(toks & _TREND_TOKENS)
    wants_metric = bool(toks & _METRIC_TOKENS)

    # Retrieval is cheap and almost always useful for a "why" question.
    if wants_why or wants_trend:
        steps.append(PlanStep(
            capability="retrieval",
            reason="'why'/'trend' question needs document + row evidence",
            parameters={"top_k": 20},
        ))

    # Metric-looking questions should evaluate KPIs.
    if wants_metric or wants_why:
        steps.append(PlanStep(
            capability="kpi",
            reason="metric or causal question triggers KPI evaluation",
        ))

    if wants_anomaly:
        steps.append(PlanStep(
            capability="anomaly",
            reason="query mentions anomalies/unusual behavior",
        ))

    if wants_forecast:
        steps.append(PlanStep(
            capability="forecast",
            reason="query mentions forecasting/prediction",
            # Explicit specs come from the LLM router or the caller.
            # The rules tier can't infer which series to forecast.
            parameters={},
        ))

    # Domain workflows take precedence over generic capabilities when the
    # query clearly belongs to a domain.
    matched_workflows = match_workflows(request.query)
    if matched_workflows:
        # Pick the first (registry iteration is stable). A future ranking
        # would prefer the workflow with the most trigger-keyword overlap.
        wf_key = matched_workflows[0]
        # Replace any generic steps with a single workflow step.
        return Plan(
            steps=[
                PlanStep(
                    capability="workflow",
                    reason=f"query matched domain '{wf_key.split('.')[0]}'",
                    parameters={"workflow_key": wf_key},
                ),
            ],
            routing_method="rules",
            intent={"workflow_key": wf_key, "tokens": sorted(toks)},
        )

    if not steps:
        return None

    return Plan(steps=steps, routing_method="rules", intent={"tokens": sorted(toks)})


# ---------- LLM routing ----------

_ROUTING_SYSTEM = (
    "You are Sansa's planner. Given a user's question, decide which "
    "capabilities Sansa should invoke to answer it. "
    "Available capabilities:\n"
    "  - retrieval: search documents and structured rows for evidence\n"
    "  - kpi: evaluate named metrics (e.g. total spend, gross margin)\n"
    "  - anomaly: find anomalous points in time series\n"
    "  - forecast: project a series into the future\n"
    "  - workflow: run a domain-specific multi-step process. If you "
    "    choose this, set parameters.workflow_key to one of the "
    "    registered workflows (the caller will provide the list).\n"
    "Respond with a JSON object of the form: "
    '{"steps": [{"capability": "...", "reason": "...", '
    '"parameters": {}}]}. '
    "Only include capabilities that are genuinely useful for this question. "
    "If nothing is clearly needed, return {\"steps\": []}. "
    "For forecast steps, include parameters.specs as a list of "
    '{"value_concept": "...", "group_by_concept": "..." | null, '
    '"horizon": integer} where you can infer them.'
)


async def _llm_plan(request: OrchestratorRequest) -> Plan | None:
    provider = get_llm_provider()
    try:
        resp = await provider.generate(
            system=_ROUTING_SYSTEM,
            messages=[{"role": "user", "content": request.query}],
            tools=None,
        )
    except LLMProviderError:
        return None

    # The mock provider returns a fixed response with no summary we can
    # parse. Detect that by checking for a "steps"-shaped payload in raw.
    # In practice the mock always falls back to run-all.
    if resp.raw is None:
        return None

    raw = resp.raw
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw, dict):
        return None
    step_specs = raw.get("steps")
    if not isinstance(step_specs, list):
        return None

    steps: list[PlanStep] = []
    for s in step_specs:
        if not isinstance(s, dict):
            continue
        cap = s.get("capability")
        if not isinstance(cap, str):
            continue
        raw_params = s.get("parameters")
        params = (
            {key: value for key, value in raw_params.items() if isinstance(key, str)}
            if isinstance(raw_params, dict)
            else {}
        )
        steps.append(PlanStep(
            capability=cap,
            reason=str(s.get("reason") or "llm-routed"),
            parameters=params,
        ))

    return Plan(steps=steps, routing_method="llm", intent={})


# ---------- run-all fallback ----------

def _all_plan() -> Plan:
    return Plan(
        steps=[
            PlanStep(capability="retrieval", reason="run-all fallback",
                     parameters={"top_k": 20}),
            PlanStep(capability="kpi", reason="run-all fallback"),
            PlanStep(capability="anomaly", reason="run-all fallback"),
        ],
        routing_method="all",
        intent={},
    )


async def plan(request: OrchestratorRequest) -> Plan:
    """
    Top-level entry point. Tries rules, then LLM, then run-all.

    `all` never includes `forecast` — that requires explicit specs and
    would just raise CapabilityError. Forecast is only added when a
    caller or the LLM provides specs.
    """
    rules = _rules_plan(request)
    if rules is not None and rules.steps:
        return rules

    llm = await _llm_plan(request)
    if llm is not None and llm.steps:
        return llm

    return _all_plan()