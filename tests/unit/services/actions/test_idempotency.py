# tests/unit/services/actions/test_idempotency.py
"""
Finding 9: an external side effect and a database commit are independent.

If the provider succeeds and the commit fails, a retry must not send the
same email twice.
"""

import uuid
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.actions import service as action_service
from app.services.actions.base import ActionError, ActionResult, VerificationOutcome


class _Tool:
    name = "test_send"
    risk_level = "send"

    def __init__(self):
        self.sends = 0

    async def execute(self, *, db, payload, context):
        self.sends += 1
        return ActionResult(output={"provider_id": "m-1"})

    async def verify(self, *, db, payload, context, result):
        return VerificationOutcome(verified=True, detail={"found": True})


class _Params:
    @classmethod
    def model_validate(cls, value):
        return value


_Tool.parameters_model = _Params


class _Ctx:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    decision_run_id = None


class _Db:
    def __init__(self):
        self.flushes = 0
        self.committed = False

    async def flush(self):
        self.flushes += 1

    async def commit(self):
        self.committed = True


def _record():
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=_Ctx.tenant_id,
        user_id=_Ctx.user_id,
        decision_run_id=None,
        tool_name="test_send",
        status="approved",
        arguments={},
        idempotency_key=None,
        attempt_count=0,
        execution_result={},
        verification_result={},
        verification_error=None,
        verified_at=None,
        error_message=None,
        finished_at=None,
        duration_ms=0,
        started_at=None,
    )


@pytest.fixture(autouse=True)
def _silence_events(monkeypatch):
    async def _emit(db, **kwargs):
        return None

    monkeypatch.setattr(action_service, "emit_event", _emit)


@pytest.mark.asyncio
async def test_attempt_is_recorded_before_the_side_effect():
    tool = _Tool()
    record = _record()

    seen: dict = {}

    original_execute = tool.execute

    async def _execute(*, db, payload, context):
        # At the moment the provider is called, the attempt must already be
        # durable — otherwise a crash here leaves no trace of the send.
        seen["attempt_count"] = record.attempt_count
        seen["status"] = record.status
        seen["key"] = record.idempotency_key
        seen["committed"] = db.committed
        seen["context_key"] = context.idempotency_key
        return await original_execute(db=db, payload=payload, context=context)

    tool.execute = _execute

    db = _Db()
    await action_service._run_and_verify(
        cast(AsyncSession, db),
        record=cast(Any, record),
        action=tool,
        payload={},
        context=cast(Any, _Ctx()),
    )

    assert seen["attempt_count"] == 1
    assert seen["status"] == "executing"
    assert seen["key"] == f"action:{record.id}"
    assert seen["committed"] is True
    assert seen["context_key"] == f"action:{record.id}"


@pytest.mark.asyncio
async def test_a_crashed_attempt_is_not_silently_retried():
    """
    A record left in 'executing' by a crash must refuse to execute again:
    the first send may have reached the recipient.
    """
    tool = _Tool()
    record = _record()
    record.status = "executing"
    record.attempt_count = 1
    record.idempotency_key = f"action:{record.id}"

    with pytest.raises(ActionError, match="reconcile before retrying"):
        await action_service.execute_approved(
            cast(AsyncSession, _Db()),
            record=cast(Any, record),
            actor_user_id=uuid.uuid4(),
        )

    assert tool.sends == 0


@pytest.mark.asyncio
async def test_reconcile_confirms_the_side_effect_without_resending(monkeypatch):
    tool = _Tool()
    record = _record()
    record.status = "executing"
    record.attempt_count = 1
    record.idempotency_key = f"action:{record.id}"
    record.execution_result = {"output": {"provider_id": "m-1"}}

    monkeypatch.setattr(action_service, "get_action", lambda name: tool)

    resolved = await action_service.reconcile_action(
        cast(AsyncSession, _Db()), record=cast(Any, record)
    )

    assert resolved.status == "executed"
    assert tool.sends == 0
    assert resolved.verification_result["reconciled"] is True


@pytest.mark.asyncio
async def test_reconcile_that_cannot_confirm_leaves_it_unresolved(monkeypatch):
    """Sending twice is worse than an unresolved record."""

    class _Uncertain(_Tool):
        async def verify(self, *, db, payload, context, result):
            raise RuntimeError("provider unavailable")

    tool = _Uncertain()
    record = _record()
    record.status = "executing"
    record.attempt_count = 1
    record.execution_result = {"output": {"provider_id": "m-1"}}

    monkeypatch.setattr(action_service, "get_action", lambda name: tool)

    resolved = await action_service.reconcile_action(
        cast(AsyncSession, _Db()), record=cast(Any, record)
    )

    assert resolved.status == "verification_failed"
    assert tool.sends == 0
    assert "reconciliation raised" in (resolved.verification_error or "")
