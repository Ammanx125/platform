# tests/unit/orchestration/test_planner.py
import pytest

from app.services.orchestration.context import OrchestratorRequest
from app.services.orchestration.planner import plan


@pytest.mark.asyncio
async def test_why_question_triggers_retrieval_and_kpi():
    p = await plan(OrchestratorRequest(query="Why did procurement cost increase?"))
    caps = {s.capability for s in p.steps}
    assert "retrieval" in caps
    assert "kpi" in caps


@pytest.mark.asyncio
async def test_anomaly_question_triggers_anomaly():
    p = await plan(OrchestratorRequest(query="Any unusual supplier prices this month?"))
    caps = {s.capability for s in p.steps}
    assert "anomaly" in caps


@pytest.mark.asyncio
async def test_forecast_question_triggers_forecast():
    p = await plan(OrchestratorRequest(query="Forecast revenue for next quarter"))
    caps = {s.capability for s in p.steps}
    assert "forecast" in caps


@pytest.mark.asyncio
async def test_unmatched_query_falls_through_to_all():
    p = await plan(OrchestratorRequest(query="hello"))
    assert p.routing_method == "all"
    caps = {s.capability for s in p.steps}
    assert caps == {"retrieval", "kpi", "anomaly"}
    assert "forecast" not in caps