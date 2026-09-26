# app/services/actions/service.py
"""
Action handling: validate, authorize, resolve policy, execute, verify.

Two entry points:

  handle_proposals()      — called by the orchestrator with the LLM's tool
                            calls. Full pipeline: validate, authorize, risk
                            policy, execute (or queue for approval), verify.

  execute_approved()      — called by approval.py when a pending_approval
                            record is approved. Runs execute() and verify().

Every proposal produces exactly one ActionRecord, regardless of outcome.
Every state transition emits an AuditEvent.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.action import ActionRecord
from app.db.models.user import User
from app.services.actions import policy as policy_service
from app.services.actions.audit import emit_event
from app.services.actions.base import (
    ActionContext,
    ActionError,
    VerificationOutcome,
)
from app.services.actions.registry import get as get_action
from app.services.actions.validators import run_validators
from app.services.llm.schemas import ToolCall
from app.services.security.redaction import redact_dict


async def handle_proposals(
    db: AsyncSession,
    *,
    tool_calls: list[ToolCall],
    context: ActionContext,
) -> list[ActionRecord]:
    """
    Process the LLM's tool calls. Returns one ActionRecord per call.

    Does not commit — the caller commits.
    """
    records: list[ActionRecord] = []
    for call in tool_calls:
        record = await _handle_one(db, call=call, context=context)
        records.append(record)
    return records


async def _handle_one(
    db: AsyncSession,
    *,
    call: ToolCall,
    context: ActionContext,
) -> ActionRecord:
    proposed_at = datetime.now(UTC)

    record = ActionRecord(
        tenant_id=context.tenant_id,
        user_id=context.user_id,
        decision_run_id=context.decision_run_id,
        tool_name=call.name,
        arguments=redact_dict(dict(call.arguments)),
        rationale=call.rationale,
        status="rejected",     # optimistic default; overwritten below
        proposed_at=proposed_at,
    )
    db.add(record)
    await db.flush()

    # 1. Tool lookup.
    action = get_action(call.name)
    if action is None:
        record.status = "rejected"
        record.rejection_reason = f"unknown tool: {call.name!r}"
        record.validation_result = {
            "schema": {"passed": False, "error": "unknown tool"},
            "permission": {"passed": False, "error": "unknown tool"},
        }
        await _audit_rejected(db, record=record, context=context)
        return record

    # 2. Schema validation.
    try:
        payload = action.parameters_model.model_validate(call.arguments)
    except ValidationError as exc:
        record.status = "rejected"
        record.rejection_reason = "argument schema validation failed"
        record.validation_result = {
            "schema": {"passed": False, "error": str(exc)[:500]},
            "permission": {"passed": False, "error": "not evaluated"},
            "risk_level": action.risk_level,
        }
        await _audit_rejected(db, record=record, context=context)
        return record

    # 3. Permission check.
    user = (
        await db.execute(select(User).where(User.id == context.user_id))
    ).scalar_one_or_none()
    if user is None or user.tenant_id != context.tenant_id or not user.is_active:
        record.status = "rejected"
        record.rejection_reason = "user not authorized for this tenant"
        record.validation_result = {
            "schema": {"passed": True},
            "permission": {"passed": False, "error": "tenant mismatch or inactive"},
            "risk_level": action.risk_level,
        }
        await _audit_rejected(db, record=record, context=context)
        return record

    # 4. Per-concern validators.
    validation_runs = run_validators(action.validators, payload, context)
    failed = next(
        ((name, out) for name, out in validation_runs if not out.passed),
        None,
    )

    validation_result: dict = {
        "schema": {"passed": True},
        "permission": {"passed": True},
        "risk_level": action.risk_level,
        "validators": [
            {
                "name": name,
                "passed": out.passed,
                "error": out.error,
                "detail": out.detail,
            }
            for name, out in validation_runs
        ],
    }
    record.validation_result = validation_result

    if failed is not None:
        record.status = "rejected"
        record.rejection_reason = failed[1].error or "validator failed"
        await _audit_rejected(db, record=record, context=context)
        return record

    # 5. Resolve effective policy.
    effective = await policy_service.resolve(
        db,
        tenant_id=context.tenant_id,
        tool_name=action.name,
        tool_risk_level=action.risk_level,
    )
    validation_result["effective_policy"] = effective
    record.validation_result = validation_result

    if effective == policy_service.POLICY_DISABLED:
        record.status = "rejected"
        record.rejection_reason = (
            f"tool {action.name!r} is disabled for this tenant"
        )
        await _audit_rejected(db, record=record, context=context)
        return record

    if effective == policy_service.POLICY_REQUIRE_APPROVAL:
        record.status = "pending_approval"
        await db.flush()
        await emit_event(
            db,
            tenant_id=context.tenant_id,
            event_type="action.pending_approval",
            actor_user_id=context.user_id,
            subject_type="action",
            subject_id=record.id,
            metadata={
                "tool_name": record.tool_name,
                "risk_level": action.risk_level,
                "effective_policy": effective,
            },
            message=f"{action.name} queued for approval",
        )
        return record

    # 6. Auto-execute.
    await _run_and_verify(db, record=record, action=action, payload=payload, context=context)
    return record


async def execute_approved(
    db: AsyncSession,
    *,
    record: ActionRecord,
    actor_user_id,
) -> ActionRecord:
    """
    Execute a previously pending_approval record. Called by approval.py
    after approval is recorded.

    The record is expected to already have status='approved'. This function
    re-loads the action, re-validates the arguments (defensive — someone
    could have edited the record), and runs the pipeline.
    """
    action = get_action(record.tool_name)
    if action is None:
        record.status = "failed"
        record.error_message = f"tool {record.tool_name!r} no longer registered"
        await db.flush()
        await emit_event(
            db,
            tenant_id=record.tenant_id,
            event_type="action.failed",
            actor_user_id=actor_user_id,
            subject_type="action",
            subject_id=record.id,
            metadata={"reason": "tool no longer registered"},
        )
        return record

    try:
        payload = action.parameters_model.model_validate(record.arguments)
    except ValidationError as exc:
        record.status = "failed"
        record.error_message = f"arguments no longer valid: {exc}"
        await db.flush()
        await emit_event(
            db,
            tenant_id=record.tenant_id,
            event_type="action.failed",
            actor_user_id=actor_user_id,
            subject_type="action",
            subject_id=record.id,
            metadata={"reason": "arguments invalid at execution time"},
        )
        return record

    context = ActionContext(
        tenant_id=record.tenant_id,
        user_id=record.user_id,
        decision_run_id=record.decision_run_id,
    )
    await _run_and_verify(db, record=record, action=action, payload=payload, context=context)
    return record


async def _run_and_verify(
    db: AsyncSession,
    *,
    record: ActionRecord,
    action,
    payload,
    context: ActionContext,
) -> None:
    """
    Execute the action, then verify it. Updates the record in place.
    The caller is responsible for committing.
    """
    record.status = "executing"
    record.started_at = datetime.now(UTC)
    t0 = time.perf_counter()

    try:
        result = await action.execute(db=db, payload=payload, context=context)
    except ActionError as exc:
        record.status = "failed"
        record.error_message = str(exc)
        record.execution_result = {"error": str(exc)}
        record.finished_at = datetime.now(UTC)
        record.duration_ms = int((time.perf_counter() - t0) * 1000)
        await db.flush()
        await emit_event(
            db,
            tenant_id=context.tenant_id,
            event_type="action.failed",
            actor_user_id=context.user_id,
            subject_type="action",
            subject_id=record.id,
            metadata={"error": str(exc), "tool_name": record.tool_name},
        )
        return
    except Exception as exc:  # noqa: BLE001
        record.status = "failed"
        record.error_message = f"unexpected error: {exc}"
        record.execution_result = {"error": str(exc)}
        record.finished_at = datetime.now(UTC)
        record.duration_ms = int((time.perf_counter() - t0) * 1000)
        await db.flush()
        await emit_event(
            db,
            tenant_id=context.tenant_id,
            event_type="action.failed",
            actor_user_id=context.user_id,
            subject_type="action",
            subject_id=record.id,
            metadata={"error": str(exc), "tool_name": record.tool_name},
        )
        return

    record.execution_result = {"output": result.output}

    # Verify.
    verification: VerificationOutcome = await action.verify(
        db=db,
        payload=payload,
        context=context,
        result=result,
    )
    record.verification_result = {
        "verified": verification.verified,
        "detail": verification.detail,
        "error": verification.error,
    }
    record.verification_error = verification.error
    record.verified_at = datetime.now(UTC)

    if verification.verified:
        record.status = "executed"
    else:
        # The side effect may or may not have happened; verification could
        # not confirm it. The record is honest about this.
        record.status = "verification_failed"

    record.finished_at = datetime.now(UTC)
    record.duration_ms = int((time.perf_counter() - t0) * 1000)
    await db.flush()

    event_type = (
        "action.executed" if verification.verified
        else "action.verification_failed"
    )
    await emit_event(
        db,
        tenant_id=context.tenant_id,
        event_type=event_type,
        actor_user_id=context.user_id,
        subject_type="action",
        subject_id=record.id,
        metadata={
            "tool_name": record.tool_name,
            "verified": verification.verified,
            "duration_ms": record.duration_ms,
        },
        message=(
            f"{record.tool_name} executed"
            + ("" if verification.verified else " but verification failed")
        ),
    )


async def _audit_rejected(
    db: AsyncSession,
    *,
    record: ActionRecord,
    context: ActionContext,
) -> None:
    await emit_event(
        db,
        tenant_id=context.tenant_id,
        event_type="action.rejected",
        actor_user_id=context.user_id,
        subject_type="action",
        subject_id=record.id,
        metadata={
            "tool_name": record.tool_name,
            "reason": record.rejection_reason,
        },
        message=record.rejection_reason,
    )