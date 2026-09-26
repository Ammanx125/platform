# tests/unit/workflows/test_engine.py
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionLocal
from app.services.workflows.base import (
    Step,
    WorkflowRequirement,
)
from app.services.workflows.engine import start_workflow
from app.services.workflows.registry import register


@pytest_asyncio.fixture
async def db(two_tenants: dict) -> AsyncGenerator[AsyncSession]:
    # Close the workflow session before two_tenants removes its rows.
    _ = two_tenants
    async with SessionLocal() as session:
        yield session


class _SimpleWorkflow:
    key = "test.simple"
    display_name = "Test Simple"
    description = "A workflow with no pauses."
    domain = "test"
    trigger_keywords = frozenset()
    requirements: list[WorkflowRequirement] = []
    steps = [
        Step(name="set_flag", type="set_context", params={"updates": {"done": True}}),
    ]


@pytest.mark.asyncio
async def test_start_and_complete(db, two_tenants):
    # Register the workflow once (idempotent guard if this test runs twice).
    from app.services.workflows.registry import get as get_wf
    if get_wf("test.simple") is None:
        register(_SimpleWorkflow())

    instance = await start_workflow(
        db,
        workflow_key="test.simple",
        tenant_id=two_tenants["tenant_a"],
        user_id=two_tenants["user_a"],
        run_now=True,
    )
    assert instance.status == "completed"
    assert instance.workflow_context.get("done") is True