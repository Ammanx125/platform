# tests/unit/actions/test_service.py
import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionLocal
from app.services.actions.base import ActionContext
from app.services.actions.service import handle_proposals
from app.services.llm.schemas import ToolCall


@pytest_asyncio.fixture
async def db(two_tenants: dict) -> AsyncGenerator[AsyncSession]:
    # Close/rollback the action session before two_tenants deletes its rows.
    _ = two_tenants
    async with SessionLocal() as session:
        yield session


@pytest.fixture
def ctx() -> ActionContext:
    return ActionContext(
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        decision_run_id=None,
        evidence_ids=["chunk-1"],
    )


@pytest.mark.asyncio
async def test_unknown_tool_rejected(db, ctx, two_tenants):
    # two_tenants provides a real user for the tenant
    ctx.tenant_id = two_tenants["tenant_a"]
    ctx.user_id = two_tenants["user_a"]

    records = await handle_proposals(
        db,
        tool_calls=[ToolCall(name="not_a_tool", arguments={})],
        context=ctx,
    )
    assert len(records) == 1
    assert records[0].status == "rejected"
    assert "unknown tool" in (records[0].rejection_reason or "")


@pytest.mark.asyncio
async def test_invalid_arguments_rejected(db, ctx, two_tenants):
    ctx.tenant_id = two_tenants["tenant_a"]
    ctx.user_id = two_tenants["user_a"]

    records = await handle_proposals(
        db,
        tool_calls=[ToolCall(name="list_suppliers", arguments={"limit": -5})],
        context=ctx,
    )
    assert records[0].status == "rejected"
    assert "schema validation" in (records[0].rejection_reason or "")


@pytest.mark.asyncio
async def test_valid_read_tool_executes(db, ctx, two_tenants):
    ctx.tenant_id = two_tenants["tenant_a"]
    ctx.user_id = two_tenants["user_a"]

    records = await handle_proposals(
        db,
        tool_calls=[ToolCall(name="list_suppliers", arguments={"limit": 5})],
        context=ctx,
    )
    assert records[0].status == "executed"
    assert "count" in records[0].execution_result.get("output", {})


@pytest.mark.asyncio
async def test_report_tool_requires_evidence(db, ctx, two_tenants):
    ctx.tenant_id = two_tenants["tenant_a"]
    ctx.user_id = two_tenants["user_a"]
    ctx.evidence_ids = []  # no evidence

    records = await handle_proposals(
        db,
        tool_calls=[ToolCall(
            name="generate_report",
            arguments={"title": "Test", "body": "This is a report body long enough."},
        )],
        context=ctx,
    )
    assert records[0].status == "rejected"
    assert "evidence" in (records[0].rejection_reason or "").lower()