# app/services/workflows/engine.py
"""
Workflow engine.

Runs a workflow instance step by step. Supports pause (for approvals) and
resume (from an API endpoint or, later, a worker).

This module executes steps; it does not decide when a workflow runs or
how an instance is triggered. Those belong to the orchestrator (for
user-triggered decisions) and, in Step 17, to the event/worker layer.

Execution is synchronous and sequential. Parallel step execution is a
future addition behind the same interface.
"""
from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.action import ActionRecord
from app.db.models.semantic import SemanticMapping
from app.db.models.workflow import WorkflowInstance
from app.services.actions.base import ActionContext
from app.services.actions.service import handle_proposals
from app.services.llm.schemas import ToolCall
from app.services.orchestration.capabilities import get_capability
from app.services.orchestration.context import EvidenceItem
from app.services.workflows.base import (
    Step,
    StepResult,
    Workflow,
    WorkflowContext,
)
from app.services.workflows.registry import get as get_workflow


class WorkflowError(Exception):
    """The workflow could not proceed: unknown step type, bad params, etc."""


async def _load_instance_locked(
    db: AsyncSession, *, instance_id: uuid.UUID, tenant_id: uuid.UUID
) -> WorkflowInstance:
    """
    Load a workflow instance with a row lock, or raise WorkflowError if
    not found. Callers hold the lock until the surrounding transaction
    commits or rolls back.
    """
    instance = (
        await db.execute(
            select(WorkflowInstance)
            .where(
                WorkflowInstance.id == instance_id,
                WorkflowInstance.tenant_id == tenant_id,
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if instance is None:
        raise WorkflowError("instance not found")
    return instance

async def approve_instance(
    db: AsyncSession,
    *,
    instance_id: uuid.UUID,
    tenant_id: uuid.UUID,
    approver_user_id: uuid.UUID,
    note: str | None = None,
) -> WorkflowInstance:
    """
    Approve a workflow waiting on workflow-level approval, then resume it.

    The transition from waiting_workflow_approval to running is atomic:
    the row is locked, the status is verified, and the transition happens
    inside the lock. A second concurrent approver sees the post-transition
    state and gets WorkflowError.
    """
    from app.services.audit import service as audit_service
    from app.services.audit import types as audit_types

    instance = await _load_instance_locked(
        db, instance_id=instance_id, tenant_id=tenant_id
    )
    if instance.status != "waiting_workflow_approval":
        raise WorkflowError(
            f"instance is in status {instance.status!r}, "
            f"expected 'waiting_workflow_approval'"
        )
    if instance.triggered_by_user_id == approver_user_id:
        raise WorkflowError(
            "the triggering user cannot approve their own workflow"
        )

    await audit_service.emit(
        db,
        tenant_id=tenant_id,
        event_type=audit_types.WORKFLOW_APPROVED,
        actor_user_id=approver_user_id,
        subject_type="workflow_instance",
        subject_id=instance.id,
        metadata={
            "workflow_key": instance.workflow_key,
            "note": note,
        },
    )

    return await resume_instance(db, instance=instance, user_id=approver_user_id)

async def reject_instance(
    db: AsyncSession,
    *,
    instance_id: uuid.UUID,
    tenant_id: uuid.UUID,
    rejector_user_id: uuid.UUID,
    reason: str | None = None,
) -> WorkflowInstance:
    from app.services.audit import service as audit_service
    from app.services.audit import types as audit_types

    instance = await _load_instance_locked(
        db, instance_id=instance_id, tenant_id=tenant_id
    )
    if instance.status != "waiting_workflow_approval":
        raise WorkflowError(
            f"instance is in status {instance.status!r}, "
            f"expected 'waiting_workflow_approval'"
        )
    if instance.triggered_by_user_id == rejector_user_id:
        raise WorkflowError(
            "the triggering user cannot reject their own workflow"
        )

    instance.status = "rejected"
    instance.error = reason or "rejected by approver"
    instance.finished_at = datetime.now(UTC)
    await db.flush()

    await audit_service.emit(
        db,
        tenant_id=tenant_id,
        event_type=audit_types.WORKFLOW_REJECTED,
        actor_user_id=rejector_user_id,
        subject_type="workflow_instance",
        subject_id=instance.id,
        metadata={
            "workflow_key": instance.workflow_key,
            "reason": instance.error,
        },
        message=f"workflow {instance.workflow_key} rejected",
    )
    return instance

# ---------- requirement checking ----------

async def check_requirements(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    workflow: Workflow,
) -> dict[str, Any]:
    """
    Check the workflow's declared requirements against the tenant's
    confirmed mappings.

    Returns a report:
      {
        "available": [{"kind":..., "key":...}, ...],
        "missing_optional": [...],
        "missing_required": [...],
      }
    """
    if not workflow.requirements:
        return {"available": [], "missing_optional": [], "missing_required": []}

    # Load the tenant's confirmed concept mappings once.
    confirmed_concepts = set(
        (
            await db.execute(
                select(SemanticMapping.canonical_concept_key).where(
                    SemanticMapping.tenant_id == tenant_id,
                    SemanticMapping.status == "confirmed",
                )
            )
        ).scalars().all()
    )

    available: list[dict[str, str]] = []
    missing_optional: list[dict[str, str]] = []
    missing_required: list[dict[str, str]] = []

    for r in workflow.requirements:
        if r.kind == "concept":
            present = r.key in confirmed_concepts
        elif r.kind == "kpi":
            from app.db.models.analytics import KPIDefinition
            present = (
                await db.execute(
                    select(KPIDefinition.id).where(KPIDefinition.key == r.key)
                )
            ).scalar_one_or_none() is not None
        elif r.kind == "detector":
            from app.db.models.analytics import AnomalyDetectorDefinition
            present = (
                await db.execute(
                    select(AnomalyDetectorDefinition.id).where(
                        AnomalyDetectorDefinition.key == r.key
                    )
                )
            ).scalar_one_or_none() is not None
        elif r.kind == "tool":
            from app.services.actions.registry import get as get_action
            present = get_action(r.key) is not None
        else:
            present = False

        entry = {"kind": r.kind, "key": r.key}
        if present:
            available.append(entry)
        elif r.optional:
            missing_optional.append(entry)
        else:
            missing_required.append(entry)

    return {
        "available": available,
        "missing_optional": missing_optional,
        "missing_required": missing_required,
    }


# ---------- starting an instance ----------

async def start_workflow(
    db: AsyncSession,
    *,
    workflow_key: str,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    source_ids: list[uuid.UUID] | None = None,
    trigger_type: str = "manual",
    trigger_metadata: dict[str, Any] | None = None,
    decision_run_id: uuid.UUID | None = None,
    run_now: bool = True,
    idempotency_key: str | None = None,
) -> WorkflowInstance:
    """
    Create a workflow instance, optionally running it to pause or completion.

    run_now=True (default) runs the instance synchronously; useful for
    manual start and testing. Step 17 will add a worker that picks up
    instances created with run_now=False.
    """
    workflow = get_workflow(workflow_key)
    if workflow is None:
        raise WorkflowError(f"unknown workflow: {workflow_key!r}")

    # Check requirements against the tenant; record the report.
    report = await check_requirements(db, tenant_id=tenant_id, workflow=workflow)

    meta = dict(trigger_metadata or {})
    meta["availability_report"] = report

    instance_values = {
        "tenant_id": tenant_id,
        "workflow_key": workflow_key,
        "status": "pending",
        "step_states": [
            {
                "name": s.name,
                "type": s.type,
                "status": "pending",
                "output": {},
                "started_at": None,
                "finished_at": None,
                "error": None,
            }
            for s in workflow.steps
        ],
        "workflow_context": {},
        "triggered_by_user_id": user_id,
        "trigger_type": trigger_type,
        "trigger_metadata": meta,
        "decision_run_id": decision_run_id,
        "idempotency_key": idempotency_key,
    }

    if idempotency_key is not None:
        stmt = (
            pg_insert(WorkflowInstance)
            .values(id=uuid.uuid4(), **instance_values)
            .on_conflict_do_nothing(
                index_elements=["tenant_id", "idempotency_key"],
                index_where=text("idempotency_key IS NOT NULL"),
            )
            .returning(WorkflowInstance)
        )
        result = await db.execute(stmt)
        instance = result.scalar_one_or_none()
        if instance is None:
            instance = (
                await db.execute(
                    select(WorkflowInstance).where(
                        WorkflowInstance.tenant_id == tenant_id,
                        WorkflowInstance.idempotency_key == idempotency_key,
                    )
                )
            ).scalar_one()
            return instance
    else:
        instance = WorkflowInstance(**instance_values)
        db.add(instance)
        await db.flush()

    if run_now:
        await run_instance(db, instance=instance, context=WorkflowContext(
            tenant_id=tenant_id,
            user_id=user_id,
            decision_run_id=decision_run_id,
            source_ids=list(source_ids or []),
            bag=dict(instance.workflow_context),
        ))
    return instance


# ---------- running an instance ----------

async def _emit_failed(
    db: AsyncSession,
    *,
    instance: WorkflowInstance,
    user_id: uuid.UUID | None,
) -> None:
    from app.services.audit import service as audit_service
    from app.services.audit import types as audit_types

    await audit_service.emit(
        db,
        tenant_id=instance.tenant_id,
        event_type=audit_types.WORKFLOW_FAILED,
        actor_user_id=user_id,
        subject_type="workflow_instance",
        subject_id=instance.id,
        metadata={
            "workflow_key": instance.workflow_key,
            "error": (instance.error or "")[:500] or None,
        },
        message=f"workflow {instance.workflow_key} failed",
    )


async def run_instance(
    db: AsyncSession,
    *,
    instance: WorkflowInstance,
    context: WorkflowContext,
) -> WorkflowInstance:
    """
    Run steps in order from the first incomplete step until the workflow
    pauses (waiting_*), completes, or fails.

    Idempotent on resume: a step already marked completed is skipped.
    """
    workflow = get_workflow(instance.workflow_key)
    if workflow is None:
        instance.status = "failed"
        instance.error = f"workflow {instance.workflow_key!r} no longer registered"
        instance.finished_at = datetime.now(UTC)
        await _emit_failed(db, instance=instance, user_id=context.user_id)
        await db.flush()
        return instance

    instance.status = "running"
    if instance.started_at is None:
        instance.started_at = datetime.now(UTC)
    await db.flush()

    start_wall = time.perf_counter()

    for idx, step_def in enumerate(workflow.steps):
        state = instance.step_states[idx]
        if state.get("status") == "completed":
            continue

        instance.current_step = step_def.name
        state["status"] = "running"
        state["started_at"] = datetime.now(UTC).isoformat()
        await db.flush()

        try:
            result = await _run_step(
                db, instance=instance, context=context, step=step_def,
            )
        except WorkflowError as exc:
            state["status"] = "failed"
            state["finished_at"] = datetime.now(UTC).isoformat()
            state["error"] = str(exc)
            instance.status = "failed"
            instance.error = str(exc)
            instance.finished_at = datetime.now(UTC)
            instance.duration_ms = int((time.perf_counter() - start_wall) * 1000)
            instance.workflow_context = dict(context.bag)
            await _emit_failed(db, instance=instance, user_id=context.user_id)
            await db.flush()
            return instance
        except Exception as exc:  # noqa: BLE001
            state["status"] = "failed"
            state["finished_at"] = datetime.now(UTC).isoformat()
            state["error"] = f"unexpected error: {exc}"
            instance.status = "failed"
            instance.error = f"step {step_def.name!r} failed: {exc}"
            instance.finished_at = datetime.now(UTC)
            instance.duration_ms = int((time.perf_counter() - start_wall) * 1000)
            instance.workflow_context = dict(context.bag)
            await _emit_failed(db, instance=instance, user_id=context.user_id)
            await db.flush()
            return instance

        if result.status == "completed":
            state["status"] = "completed"
            state["output"] = result.output
            state["finished_at"] = datetime.now(UTC).isoformat()
            instance.workflow_context = dict(context.bag)
            await db.flush()
            continue

        if result.status == "waiting":
            # Pause the workflow.
            state["status"] = "waiting"
            state["output"] = result.output
            state["wait_reason"] = result.wait_reason
            state["wait_ref"] = result.wait_ref
            if result.wait_reason == "workflow_approval":
                instance.status = "waiting_workflow_approval"
                instance.pending_approval_step = step_def.name
            elif result.wait_reason == "action_approval":
                instance.status = "waiting_action_approval"
                try:
                    instance.pending_action_id = uuid.UUID(result.wait_ref or "")
                except (ValueError, TypeError):
                    instance.pending_action_id = None
            elif result.wait_reason == "input":
                instance.status = "waiting_input"
            else:
                instance.status = "failed"
                instance.error = f"unknown wait_reason: {result.wait_reason!r}"
                await _emit_failed(db, instance=instance, user_id=context.user_id)
            instance.workflow_context = dict(context.bag)
            instance.duration_ms = int((time.perf_counter() - start_wall) * 1000)
            await db.flush()
            return instance

        # result.status == "failed"
        state["status"] = "failed"
        state["finished_at"] = datetime.now(UTC).isoformat()
        state["error"] = result.error
        instance.status = "failed"
        instance.error = result.error
        instance.finished_at = datetime.now(UTC)
        instance.duration_ms = int((time.perf_counter() - start_wall) * 1000)
        instance.workflow_context = dict(context.bag)
        await _emit_failed(db, instance=instance, user_id=context.user_id)
        await db.flush()
        return instance

    instance.status = "completed"
    instance.current_step = None
    instance.finished_at = datetime.now(UTC)
    instance.duration_ms = int((time.perf_counter() - start_wall) * 1000)
    instance.workflow_context = dict(context.bag)
    await db.flush()

    from app.services.audit import service as audit_service
    from app.services.audit import types as audit_types

    await audit_service.emit(
        db,
        tenant_id=instance.tenant_id,
        event_type=audit_types.WORKFLOW_COMPLETED,
        actor_user_id=context.user_id,
        subject_type="workflow_instance",
        subject_id=instance.id,
        metadata={
            "workflow_key": instance.workflow_key,
            "duration_ms": instance.duration_ms,
        },
        message=f"workflow {instance.workflow_key} completed",
    )
    return instance


async def resume_instance(
    db: AsyncSession,
    *,
    instance: WorkflowInstance,
    user_id: uuid.UUID,
    source_ids: list[uuid.UUID] | None = None,
) -> WorkflowInstance:
    """
    Resume a paused instance.

    Re-fetches the row with a lock to guard against a concurrent resume
    (another approver, or a worker whose condition just became true).
    """
    instance = await _load_instance_locked(
        db, instance_id=instance.id, tenant_id=instance.tenant_id
    )

    if not instance.status.startswith("waiting_"):
        raise WorkflowError(
            f"instance is in status {instance.status!r}, not waiting"
        )

    workflow = get_workflow(instance.workflow_key)
    if workflow is None:
        instance.status = "failed"
        instance.error = f"workflow {instance.workflow_key!r} no longer registered"
        instance.finished_at = datetime.now(UTC)
        await db.flush()
        return instance

    context = WorkflowContext(
        tenant_id=instance.tenant_id,
        user_id=user_id,
        decision_run_id=instance.decision_run_id,
        source_ids=list(source_ids or []),
        bag=dict(instance.workflow_context),
    )

    for state in instance.step_states:
        if state.get("status") == "waiting":
            state["status"] = "completed"
            state["finished_at"] = datetime.now(UTC).isoformat()

    instance.pending_approval_step = None
    instance.pending_action_id = None

    return await run_instance(db, instance=instance, context=context)


# ---------- step handlers ----------

async def _run_step(
    db: AsyncSession,
    *,
    instance: WorkflowInstance,
    context: WorkflowContext,
    step: Step,
) -> StepResult:
    if step.type == "analyze":
        return await _run_analyze(db, instance=instance, context=context, step=step)
    if step.type == "run_action":
        return await _run_action(db, instance=instance, context=context, step=step)
    if step.type == "await_approval":
        return await _run_await_approval(db, instance=instance, step=step)
    if step.type == "await_action":
        return await _run_await_action(db, instance=instance, context=context, step=step)
    if step.type == "set_context":
        return _run_set_context(context=context, step=step)
    raise WorkflowError(f"unhandled step type: {step.type!r}")


async def _run_analyze(
    db: AsyncSession,
    *,
    instance: WorkflowInstance,
    context: WorkflowContext,
    step: Step,
) -> StepResult:
    capability_name = step.params.get("capability")
    if not capability_name:
        raise WorkflowError(f"analyze step {step.name!r} requires 'capability'")
    capability_params = dict(step.params.get("capability_params") or {})

    try:
        capability = get_capability(capability_name)
    except Exception as exc:  # noqa: BLE001
        raise WorkflowError(
            f"analyze step {step.name!r}: {exc}"
        ) from exc

    query = str(step.params.get("query") or "")
    try:
        items: list[EvidenceItem] = await capability.run(
            db=db,
            tenant_id=instance.tenant_id,
            step_params=capability_params,
            query=query,
            source_ids=context.source_ids or None,
        )
    except Exception as exc:  # noqa: BLE001
        return StepResult(
            status="failed",
            error=f"capability {capability_name!r} failed: {exc}",
        )

    # Record items in the bag so later steps can reference them.
    bag_key = step.params.get("store_as") or f"{step.name}_evidence"
    context.bag[bag_key] = [item.id for item in items]

    return StepResult(
        status="completed",
        output={
            "capability": capability_name,
            "item_count": len(items),
            "item_ids": [item.id for item in items],
            "items": [item.data for item in items],
        },
    )


async def _run_action(
    db: AsyncSession,
    *,
    instance: WorkflowInstance,
    context: WorkflowContext,
    step: Step,
) -> StepResult:
    tool_calls_raw = step.params.get("tool_calls") or []
    if not tool_calls_raw:
        raise WorkflowError(f"run_action step {step.name!r} requires 'tool_calls'")

    tool_calls = [
        ToolCall(
            name=tc["name"],
            arguments=tc.get("arguments") or {},
            rationale=tc.get("rationale"),
        )
        for tc in tool_calls_raw
    ]

    wait_for_approval = bool(step.params.get("wait_for_approval", False))
    on_rejection = step.params.get("on_action_rejection", "fail")
    if on_rejection not in ("fail", "continue"):
        raise WorkflowError(
            f"run_action step {step.name!r}: on_action_rejection must be "
            f"'fail' or 'continue'"
        )

    action_context = ActionContext(
        tenant_id=instance.tenant_id,
        user_id=context.user_id,
        decision_run_id=instance.decision_run_id,
        source_ids=list(context.source_ids),
        evidence_ids=[],   # workflows don't thread evidence_ids through yet
    )

    records = await handle_proposals(
        db, tool_calls=tool_calls, context=action_context
    )

    summary = [
        {
            "id": str(r.id),
            "tool_name": r.tool_name,
            "status": r.status,
        }
        for r in records
    ]

    # Decide whether to wait.
    pending = [r for r in records if r.status == "pending_approval"]
    if wait_for_approval and pending:
        # Wait on the first pending action. Multi-action waits are a
        # future addition; today, workflows wait on one action at a time.
        first = pending[0]
        context.bag[f"{step.name}_action_ids"] = [str(r.id) for r in records]
        return StepResult(
            status="waiting",
            output={"actions": summary},
            wait_reason="action_approval",
            wait_ref=str(first.id),
        )

    # Immediate rejection handling.
    rejected = [r for r in records if r.status == "rejected"]
    if rejected and on_rejection == "fail":
        return StepResult(
            status="failed",
            output={"actions": summary},
            error=(
                f"action {rejected[0].tool_name!r} was rejected: "
                f"{rejected[0].rejection_reason}"
            ),
        )

    return StepResult(status="completed", output={"actions": summary})


async def _run_await_approval(
    db: AsyncSession,
    *,
    instance: WorkflowInstance,
    step: Step,
) -> StepResult:
    """
    Always returns waiting on first run. The approval endpoint calls
    resume_instance, which marks the step completed and re-runs the engine.
    """
    prompt = step.params.get("prompt") or "Approve this workflow to proceed."
    return StepResult(
        status="waiting",
        output={"prompt": prompt},
        wait_reason="workflow_approval",
        wait_ref=step.name,
    )


async def _run_await_action(
    db: AsyncSession,
    *,
    instance: WorkflowInstance,
    context: WorkflowContext,
    step: Step,
) -> StepResult:
    """
    Wait until a previously proposed action reaches a terminal status.

    action_id_from: key in the bag holding a list of action ids (set by a
    prior run_action step), or a JSON path. For simplicity, we accept:
      - a bag key whose value is a list of action-id strings (first is used)
      - a literal action UUID string
    """
    raw_ref = step.params.get("action_id_from")
    if not raw_ref:
        raise WorkflowError(
            f"await_action step {step.name!r} requires 'action_id_from'"
        )

    # Resolve from bag if it's a key.
    candidate: str | None = None
    bag_value = context.bag.get(raw_ref)
    if isinstance(bag_value, list) and bag_value:
        candidate = str(bag_value[0])
    elif isinstance(bag_value, str):
        candidate = bag_value
    else:
        candidate = raw_ref if isinstance(raw_ref, str) else None

    if not candidate:
        raise WorkflowError(
            f"await_action step {step.name!r}: could not resolve action id "
            f"from {raw_ref!r}"
        )

    try:
        action_id = uuid.UUID(candidate)
    except (ValueError, TypeError) as exc:
        raise WorkflowError(
            f"await_action step {step.name!r}: invalid action id {candidate!r}"
        ) from exc

    record = (
        await db.execute(
            select(ActionRecord).where(
                ActionRecord.id == action_id,
                ActionRecord.tenant_id == instance.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if record is None:
        return StepResult(
            status="failed",
            error=f"referenced action {action_id} not found",
        )

    terminal = {"executed", "failed", "verification_failed", "rejected"}
    if record.status not in terminal:
        # Still waiting.
        return StepResult(
            status="waiting",
            output={"action_id": str(action_id), "status": record.status},
            wait_reason="action_approval",
            wait_ref=str(action_id),
        )

    on_rejection = step.params.get("on_rejection", "fail")
    if record.status == "rejected" and on_rejection == "fail":
        return StepResult(
            status="failed",
            output={"action_id": str(action_id), "status": record.status},
            error=f"action {action_id} was rejected: {record.rejection_reason}",
        )

    return StepResult(
        status="completed",
        output={
            "action_id": str(action_id),
            "status": record.status,
            "verification": record.verification_result,
        },
    )


def _run_set_context(
    *,
    context: WorkflowContext,
    step: Step,
) -> StepResult:
    updates = step.params.get("updates") or {}
    if not isinstance(updates, dict):
        raise WorkflowError(
            f"set_context step {step.name!r}: 'updates' must be a dict"
        )
    context.bag.update(updates)
    return StepResult(status="completed", output={"updated_keys": list(updates)})


# ---------- listing / reading ----------

async def list_instances(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    status: str | None = None,
    workflow_key: str | None = None,
    limit: int = 100,
) -> list[WorkflowInstance]:
    stmt = (
        select(WorkflowInstance)
        .where(WorkflowInstance.tenant_id == tenant_id)
        .order_by(WorkflowInstance.created_at.desc())
        .limit(limit)
    )
    if status is not None:
        stmt = stmt.where(WorkflowInstance.status == status)
    if workflow_key is not None:
        stmt = stmt.where(WorkflowInstance.workflow_key == workflow_key)
    return list((await db.execute(stmt)).scalars().all())


async def get_instance(
    db: AsyncSession, *, instance_id: uuid.UUID, tenant_id: uuid.UUID
) -> WorkflowInstance | None:
    return (
        await db.execute(
            select(WorkflowInstance).where(
                WorkflowInstance.id == instance_id,
                WorkflowInstance.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()


async def cancel_instance(
    db: AsyncSession,
    *,
    instance: WorkflowInstance,
    reason: str | None = None,
) -> WorkflowInstance:
    instance = await _load_instance_locked(
        db, instance_id=instance.id, tenant_id=instance.tenant_id
    )
    if instance.status in ("completed", "failed", "rejected", "cancelled"):
        # Terminal states can't be cancelled.
        return instance
    instance.status = "cancelled"
    instance.error = reason or "cancelled by user"
    instance.finished_at = datetime.now(UTC)
    await db.flush()
    return instance