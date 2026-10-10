# tests/unit/orchestration/test_planner_email_routing.py
from __future__ import annotations

import pytest

from app.services.orchestration.context import OrchestratorRequest
from app.services.orchestration.planner import plan


@pytest.mark.asyncio
async def test_planner_routes_complaint_question_to_email_summary():
    req = OrchestratorRequest(query="how many complaints did we get today?")
    p = await plan(req)
    assert p.routing_method == "rules"
    assert any(s.capability == "email_summary" for s in p.steps)


@pytest.mark.asyncio
async def test_planner_routes_inbox_question_to_email_summary():
    req = OrchestratorRequest(query="what's in my inbox?")
    p = await plan(req)
    caps = {s.capability for s in p.steps}
    assert "email_summary" in caps


@pytest.mark.asyncio
async def test_planner_does_not_route_unrelated_question():
    req = OrchestratorRequest(query="what is our total spend?")
    p = await plan(req)
    caps = {s.capability for s in p.steps}
    assert "email_summary" not in caps