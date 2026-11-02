# app/web/view_helpers.py
"""
Presentation helpers shared by HTML routes.

These turn stored records into the row shape consumed by
``frontend/templates/workflows/_timeline.html``. They contain no
arithmetic and no database access: the timeline only reflects fields the
engine/services have already persisted (``step_states``, ``status``,
timestamps, errors).

Timeline row shape (one dict per step):

  name        step (or action lifecycle stage) label
  status      normalized: 'complete' | 'running' | 'waiting' | 'failed' | 'pending'
  detail      what the step produced, if anything
  json        True when ``detail`` should be rendered as raw JSON
  error       error message, if any
  timestamp   when this row was recorded, if the source records one
  is_approval True when this row is the step currently awaiting a human
  href        optional link for this row (used by the approval row)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

# Raw step statuses → normalized timeline statuses.
_COMPLETE = frozenset({"completed", "succeeded"})
_RUNNING = frozenset({"running", "in_progress"})
_FAILED = frozenset({"failed", "error"})


def _row(
    *,
    name: str,
    status: str,
    detail: Any = None,
    json_detail: bool = False,
    error: str | None = None,
    timestamp: datetime | str | None = None,
    is_approval: bool = False,
    href: str | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "detail": detail,
        "json": json_detail,
        "error": error,
        "timestamp": timestamp,
        "is_approval": is_approval,
        "href": href,
    }


def _timeline_timestamp(value: Any) -> datetime | str | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return value


def build_timeline(instance: Any) -> list[dict[str, Any]]:
    """
    Transform ``WorkflowInstance.step_states`` into timeline rows.

    A step that is paused on either kind of approval — a workflow-level
    ``await_approval`` step or a ``run_action`` step waiting on action
    approval — is shown as waiting. Waiting on the action approval is the
    case where ``pending_approval_step`` is not set, so the step carrying
    ``wait_reason == "action_approval"`` is the one marked instead.
    """
    rows: list[dict[str, Any]] = []
    pending_step = instance.pending_approval_step
    for state in instance.step_states or []:
        if not isinstance(state, dict):
            continue
        name = state.get("name", "")
        raw_status = (state.get("status") or "").lower()
        is_workflow_approval = name == pending_step
        is_action_approval = (
            raw_status == "waiting" and state.get("wait_reason") == "action_approval"
        )
        is_approval = is_workflow_approval or is_action_approval

        if raw_status in _COMPLETE:
            normalized = "complete"
        elif raw_status in _RUNNING:
            normalized = "running"
        elif raw_status in _FAILED:
            normalized = "failed"
        elif is_approval:
            normalized = "waiting"
        else:
            normalized = "pending"

        output = state.get("output")
        rows.append(
            _row(
                name=name,
                status=normalized,
                detail=output if output else None,
                json_detail=bool(output),
                error=state.get("error"),
                timestamp=_timeline_timestamp(state.get("finished_at") or state.get("started_at")),
                is_approval=is_approval,
                href="/approvals" if is_action_approval else None,
            )
        )
    return rows


def build_action_timeline(action: Any) -> list[dict[str, Any]]:
    """
    Transform an ``ActionRecord`` lifecycle into timeline rows.

    Proposal, decision, execution, and verification are later stages that
    did not happen: an action still pending approval ends at "Approval
    pending", a rejected one ends at "Decision", and so on. This keeps the
    timeline from implying steps that never ran.
    """
    status = (action.status or "").lower()
    execution = action.execution_result or {}

    rows: list[dict[str, Any]] = [
        _row(
            name="Proposed",
            status="complete",
            timestamp=action.proposed_at,
            detail=action.tool_name,
        )
    ]

    if status == "pending_approval":
        rows.append(_row(name="Approval pending", status="waiting", is_approval=True))
        return rows

    if status == "rejected":
        rows.append(
            _row(
                name="Decision",
                status="complete",
                detail="rejected",
                error=action.rejection_reason,
            )
        )
        return rows

    was_approved = (
        status == "approved"
        or action.approved_at is not None
        or action.approved_by_user_id is not None
    )
    if was_approved:
        detail = f"by {action.approved_by_user_id}" if action.approved_by_user_id else None
        rows.append(
            _row(
                name="Approval",
                status="complete",
                detail=detail,
                timestamp=action.approved_at,
            )
        )

    if status == "approved":
        # Approved but execution has not been recorded yet.
        rows.append(_row(name="Execution", status="running"))
        return rows

    execution_failed = status in ("failed", "verification_failed")
    rows.append(
        _row(
            name="Execution",
            status="failed" if status == "failed" else "complete",
            detail=execution.get("output") or None,
            json_detail=bool(execution.get("output")),
            error=action.error_message or execution.get("error"),
            timestamp=action.finished_at,
        )
    )
    if status == "failed":
        return rows

    rows.append(
        _row(
            name="Verification",
            status="failed" if execution_failed else "complete",
            error=action.verification_error,
            timestamp=action.verified_at,
        )
    )
    return rows
