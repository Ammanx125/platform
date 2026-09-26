# app/services/actions/service.py
"""
Action handling: validate, authorize, execute.

The single entry point is handle_proposals(), called by the orchestrator
after the LLM produces tool calls.

Every proposal — valid, invalid, authorized, unauthorized, executed,
rejected — produces exactly one ActionRecord. Rejects are first-class: they
show what was proposed and why Sansa refused.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.action import ActionRecord
from app.db.models.user import User
from app.services.actions.base import (
    RISK_ELEVATED,
    RISK_REQUIRED,
    ActionContext,
    ActionError,
)
from app.services.actions.registry import get as get_action
from app.services.actions.validators import run_validators
from app.services.llm.schemas import ToolCall

# Risk levels that always require approval. In 11a we can't obtain approval,
# so tools at these levels are rejected rather than executed. 11b flips
# this to "route to pending_approval" and lets the approval flow run.
_APPROVAL_REQUIRED_LEVELS = {RISK_REQUIRED, RISK_ELEVATED}


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
        arguments=dict(call.arguments),
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
        return record

    # 3. Permission check.
    # A tool's risk level determines who can invoke it. In 11a we don't
    # have per-tool permission grants; we enforce tenant membership only,
    # which the orchestrator already established by providing a valid user.
    # A future iteration adds a permission:action keyed on the tool name.
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

    if failed is not None:
        record.status = "rejected"
        record.rejection_reason = failed[1].error or "validator failed"
        record.validation_result = validation_result
        return record

    # 5. Approval gate (stub in 11a).
    if action.risk_level in _APPROVAL_REQUIRED_LEVELS:
        # 11a cannot obtain approval; reject cleanly with a message that
        # tells the caller the tool exists and why it wasn't run.
        record.status = "rejected"
        record.rejection_reason = (
            f"tool {action.name!r} requires approval; "
            f"approval workflow is not enabled in this deployment"
        )
        validation_result["would_require_approval"] = True
        record.validation_result = validation_result
        return record

    # 6. Execute.
    record.status = "executing"
    record.started_at = datetime.now(UTC)
    t0 = time.perf_counter()

    try:
        result = await action.execute(db=db, payload=payload, context=context)
    except ActionError as exc:
        record.status = "failed"
        record.error_message = str(exc)
        record.execution_result = {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        record.status = "failed"
        record.error_message = f"unexpected error: {exc}"
        record.execution_result = {"error": str(exc)}
    else:
        record.status = "executed"
        record.execution_result = {"output": result.output}

    record.finished_at = datetime.now(UTC)
    record.duration_ms = int((time.perf_counter() - t0) * 1000)
    record.validation_result = validation_result
    await db.flush()
    return record