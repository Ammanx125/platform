# tests/unit/workflows/test_engine.py
import uuid as _uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.action import ActionRecord
from app.db.session import SessionLocal
from app.services.workflows import engine as wf_engine
from app.services.workflows.base import (
    Step,
    WorkflowRequirement,
)
from app.services.workflows.engine import cancel_instance, resume_instance, start_workflow
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


@pytest.mark.asyncio
async def test_start_workflow_returns_existing_idempotency_key(db, two_tenants):
    from app.services.workflows.registry import get as get_wf

    if get_wf("test.simple") is None:
        register(_SimpleWorkflow())

    first = await start_workflow(
        db,
        workflow_key="test.simple",
        tenant_id=two_tenants["tenant_a"],
        user_id=two_tenants["user_a"],
        run_now=False,
        idempotency_key="test-request-123",
    )
    duplicate = await start_workflow(
        db,
        workflow_key="test.simple",
        tenant_id=two_tenants["tenant_a"],
        user_id=two_tenants["user_a"],
        run_now=True,
        idempotency_key="test-request-123",
    )

    assert duplicate.id == first.id
    assert duplicate.status == "pending"

# ---------- finding 10A–G: workflow correctness ----------

# ---------- finding 10A–G: workflow correctness ----------


def _register(workflow) -> None:
    from app.services.workflows.registry import get as get_wf

    if get_wf(workflow.key) is None:
        register(workflow)


class _ReportWorkflow:
    """Declares a report step: needs evidence ids to reach the action."""

    key = "test.report"
    display_name = "Test Report"
    description = "Run a report action."
    domain = "test"
    trigger_keywords = frozenset()
    requirements: list[WorkflowRequirement] = []
    steps = [
        Step(
            name="report",
            type="run_action",
            params={
                "tool_calls": [
                    {
                        "name": "generate_report",
                        "arguments": {
                            "title": "Weekly report",
                            "body": "Body of the report.",
                        },
                        "rationale": "Summarise the week.",
                    }
                ]
            },
        ),
    ]


@pytest.mark.asyncio
async def test_run_action_receives_evidence_ids(monkeypatch, db, two_tenants):
    """
    Finding 10A: _run_action built its ActionContext with evidence_ids=[],
    so validate_evidence_cited always rejected generate_report and every
    workflow using it failed.
    """
    captured: dict = {}

    async def fake_handle_proposals(db, *, tool_calls, context):
        captured["evidence_ids"] = list(context.evidence_ids)
        return []

    monkeypatch.setattr(wf_engine, "handle_proposals", fake_handle_proposals)
    _register(_ReportWorkflow())

    await start_workflow(
        db,
        workflow_key="test.report",
        tenant_id=two_tenants["tenant_a"],
        user_id=two_tenants["user_a"],
        trigger_metadata={},
    )
    # The step ran with whatever the workflow gathered; the point is that the
    # context is not hard-coded to an empty list.
    assert "evidence_ids" in captured


@pytest.mark.asyncio
async def test_run_action_passes_gathered_evidence_ids(monkeypatch, db, two_tenants):
    captured: dict = {}

    async def fake_handle_proposals(db, *, tool_calls, context):
        captured["evidence_ids"] = list(context.evidence_ids)
        return []

    monkeypatch.setattr(wf_engine, "handle_proposals", fake_handle_proposals)

    class _Seeded:
        key = "test.seeded_report"
        display_name = "Seeded"
        description = "Sets evidence, then reports."
        domain = "test"
        trigger_keywords = frozenset()
        requirements: list[WorkflowRequirement] = []
        steps = [
            Step(
                name="seed",
                type="set_context",
                params={"updates": {"evidence_ids": ["kpi:revenue", "chunk:1"]}},
            ),
            _ReportWorkflow.steps[0],
        ]

    _register(_Seeded())
    await start_workflow(
        db,
        workflow_key="test.seeded_report",
        tenant_id=two_tenants["tenant_a"],
        user_id=two_tenants["user_a"],
        trigger_metadata={},
    )

    assert captured["evidence_ids"] == ["kpi:revenue", "chunk:1"]


@pytest.mark.asyncio
async def test_failed_action_fails_the_workflow(monkeypatch, db, two_tenants):
    """Finding 10B: a failed action must not yield a completed workflow."""

    def _record(status: str) -> ActionRecord:
        return ActionRecord(
            tenant_id=two_tenants["tenant_a"],
            user_id=two_tenants["user_a"],
            tool_name="generate_report",
            arguments={},
            rationale="x",
            status=status,
            validation_result={},
            execution_result={},
            proposed_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
            error_message="gmail returned 500",
        )

    async def fake_handle_proposals(db, *, tool_calls, context):
        return [_record("failed")]

    monkeypatch.setattr(wf_engine, "handle_proposals", fake_handle_proposals)
    _register(_ReportWorkflow())

    instance = await start_workflow(
        db,
        workflow_key="test.report",
        tenant_id=two_tenants["tenant_a"],
        user_id=two_tenants["user_a"],
        trigger_metadata={},
    )

    assert instance.status == "failed"
    assert "failed" in (instance.error or "")


@pytest.mark.parametrize("raises", [False, True])
@pytest.mark.asyncio
async def test_failed_step_state_is_persisted(monkeypatch, db, two_tenants, raises):
    """A failed workflow must persist the matching failed step status."""

    async def fail_step(*args, **kwargs):
        if raises:
            raise wf_engine.WorkflowError("step raised")
        return wf_engine.StepResult(status="failed", error="step returned failure")

    monkeypatch.setattr(wf_engine, "_run_step", fail_step)
    _register(_SimpleWorkflow)

    instance = await start_workflow(
        db,
        workflow_key="test.simple",
        tenant_id=two_tenants["tenant_a"],
        user_id=two_tenants["user_a"],
    )
    await db.commit()
    await db.refresh(instance, attribute_names=["step_states"])

    assert instance.status == "failed"
    assert instance.step_states[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_multiple_pending_actions_all_wait(monkeypatch, db, two_tenants):
    """Finding 10C: waiting on the first pending action let the rest slip."""

    def _pending() -> ActionRecord:
        return ActionRecord(
            tenant_id=two_tenants["tenant_a"],
            user_id=two_tenants["user_a"],
            tool_name="generate_report",
            arguments={},
            rationale="x",
            status="pending_approval",
            validation_result={},
            execution_result={},
            proposed_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        )

    async def fake_handle_proposals(db, *, tool_calls, context):
        return [_pending(), _pending(), _pending()]

    monkeypatch.setattr(wf_engine, "handle_proposals", fake_handle_proposals)

    class _Multi:
        key = "test.multi_pending"
        display_name = "Multi"
        description = "Proposes three actions."
        domain = "test"
        trigger_keywords = frozenset()
        requirements: list[WorkflowRequirement] = []
        steps = [
            Step(
                name="act",
                type="run_action",
                params={
                    "tool_calls": [
                        {"name": "generate_report", "arguments": {}, "rationale": "x"}
                    ],
                    "wait_for_approval": True,
                },
            )
        ]

    _register(_Multi())
    instance = await start_workflow(
        db,
        workflow_key="test.multi_pending",
        tenant_id=two_tenants["tenant_a"],
        user_id=two_tenants["user_a"],
        trigger_metadata={},
    )

    assert instance.status == "waiting_action_approval"
    wait_ref = instance.step_states[0]["wait_ref"]
    assert len(str(wait_ref).split(",")) == 3


@pytest.mark.asyncio
async def test_resume_blocks_while_actions_are_open(monkeypatch, db, two_tenants):
    """Finding 10D: manual resume must not skip an approval wait."""

    def _pending() -> ActionRecord:
        return ActionRecord(
            tenant_id=two_tenants["tenant_a"],
            user_id=two_tenants["user_a"],
            tool_name="generate_report",
            arguments={},
            rationale="x",
            status="pending_approval",
            validation_result={},
            execution_result={},
            proposed_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        )

    async def fake_handle_proposals(db, *, tool_calls, context):
        record = _pending()
        db.add(record)
        await db.flush()
        return [record]

    monkeypatch.setattr(wf_engine, "handle_proposals", fake_handle_proposals)

    class _Waiting:
        key = "test.waiting_actions"
        display_name = "Waiting"
        description = "Waits on an action."
        domain = "test"
        trigger_keywords = frozenset()
        requirements: list[WorkflowRequirement] = []
        steps = [
            Step(
                name="act",
                type="run_action",
                params={
                    "tool_calls": [
                        {"name": "generate_report", "arguments": {}, "rationale": "x"}
                    ],
                    "wait_for_approval": True,
                },
            )
        ]

    _register(_Waiting())
    instance = await start_workflow(
        db,
        workflow_key="test.waiting_actions",
        tenant_id=two_tenants["tenant_a"],
        user_id=two_tenants["user_a"],
        trigger_metadata={},
    )
    assert instance.status == "waiting_action_approval"

    with pytest.raises(wf_engine.WorkflowError, match="still outstanding"):
        await resume_instance(
            db, instance=instance, user_id=two_tenants["user_a"]
        )


@pytest.mark.asyncio
async def test_resume_preserves_source_scope(monkeypatch, db, two_tenants):
    """Finding 10E: resume must not drop the run's source scope."""
    source = _uuid.uuid4()
    captured: dict = {}

    async def fake_handle_proposals(db, *, tool_calls, context):
        captured["source_ids"] = list(context.source_ids)
        return []

    monkeypatch.setattr(wf_engine, "handle_proposals", fake_handle_proposals)

    class _Paused:
        key = "test.scoped"
        display_name = "Scoped"
        description = "Pauses, then reports."
        domain = "test"
        trigger_keywords = frozenset()
        requirements: list[WorkflowRequirement] = []
        steps = [
            Step(name="pause", type="await_approval", params={"prompt": "ok?"}),
            _ReportWorkflow.steps[0],
        ]

    _register(_Paused())
    instance = await start_workflow(
        db,
        workflow_key="test.scoped",
        tenant_id=two_tenants["tenant_a"],
        user_id=two_tenants["user_a"],
        source_ids=[source],
        trigger_metadata={},
    )
    assert instance.status == "waiting_workflow_approval"

    await resume_instance(db, instance=instance, user_id=two_tenants["user_a"])
    await db.commit()

    # The returned instance is a fresh load; `instance` may be stale.
    from app.services.workflows import engine as _engine

    fresh = await _engine._load_instance_locked(
        db, instance_id=instance.id, tenant_id=instance.tenant_id
    )
    assert fresh.status in ("completed", "failed"), fresh.error
    assert captured['source_ids'] == [source]


@pytest.mark.asyncio
async def test_cancel_rejects_pending_actions(monkeypatch, db, two_tenants):
    """Finding 10F: a cancelled workflow's actions must not stay approvable."""
    created: list[ActionRecord] = []

    async def fake_handle_proposals(db, *, tool_calls, context):
        record = ActionRecord(
            tenant_id=two_tenants["tenant_a"],
            user_id=two_tenants["user_a"],
            tool_name="generate_report",
            arguments={},
            rationale="x",
            status="pending_approval",
            validation_result={},
            execution_result={},
            proposed_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        )
        db.add(record)
        await db.flush()
        created.append(record)
        return [record]

    monkeypatch.setattr(wf_engine, "handle_proposals", fake_handle_proposals)

    class _CancelMe:
        key = "test.cancel"
        display_name = "Cancel"
        description = "Waits on an action."
        domain = "test"
        trigger_keywords = frozenset()
        requirements: list[WorkflowRequirement] = []
        steps = [
            Step(
                name="act",
                type="run_action",
                params={
                    "tool_calls": [
                        {"name": "generate_report", "arguments": {}, "rationale": "x"}
                    ],
                    "wait_for_approval": True,
                },
            )
        ]

    _register(_CancelMe())
    instance = await start_workflow(
        db,
        workflow_key="test.cancel",
        tenant_id=two_tenants["tenant_a"],
        user_id=two_tenants["user_a"],
        trigger_metadata={},
    )
    assert instance.status == "waiting_action_approval"

    await cancel_instance(db, instance=instance, reason="manager cancelled")
    await db.flush()

    assert created
    assert created[0].status == "rejected"
    assert "cancelled" in (created[0].rejection_reason or "")
