# tests/unit/services/actions/test_execution_safety.py
import uuid
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

# ---------- finding 9: execution safety ----------


@pytest.mark.asyncio
async def test_verify_exception_does_not_strand_the_action(monkeypatch):
    """
    Regression: verify() was called outside the try block. If it raised,
    the record stayed in 'executing' although the side effect had happened.
    """
    from app.services.actions import service as action_service
    from app.services.actions.base import ActionResult

    class _Boom:
        name = "test_boom"
        risk_level = "report"

        async def execute(self, *, db, payload, context):
            return ActionResult(output={"ok": True})

        async def verify(self, *, db, payload, context, result):
            raise RuntimeError("provider timed out")

    class _Ctx:
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        decision_run_id = None

    record = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=_Ctx.tenant_id,
        user_id=_Ctx.user_id,
        decision_run_id=None,
        tool_name="test_boom",
        status="approved",
        execution_result={},
        verification_result={},
        verification_error=None,
        verified_at=None,
        error_message=None,
        finished_at=None,
        duration_ms=0,
        started_at=None,
    )

    events: list[dict] = []

    async def _emit(db, **kwargs):
        events.append(kwargs)

    async def _flush():
        return None

    class _Db:
        flush = staticmethod(_flush)

        async def commit(self):
            return None

    monkeypatch.setattr(action_service, "emit_event", _emit)

    await action_service._run_and_verify(
        cast(AsyncSession, _Db()),
        record=cast(Any, record),
        action=_Boom(),
        payload=None,
        context=cast(Any, _Ctx()),
    )

    assert record.status == "verification_failed"
    assert "verification raised" in (record.verification_error or "")
    assert events and events[-1]["event_type"] == "action.verification_failed"


@pytest.mark.asyncio
async def test_action_service_rejects_users_without_action_propose_permission(monkeypatch):
    from app.services.actions import service as action_service
    from app.services.actions.base import ActionContext

    class _Tool:
        name = "test_tool"
        risk_level = "report"
        parameters_model: Any

        async def execute(self, *, db, payload, context):
            raise AssertionError("must not execute")

        async def verify(self, *, db, payload, context, result):
            raise AssertionError("must not verify")

    class _Params:
        @classmethod
        def model_validate(cls, value):
            return value

    _Tool.parameters_model = _Params

    class _Result:
        def __init__(self, row):
            self._row = row

        def scalar_one_or_none(self):
            return self._row

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()

    class _Db:
        def __init__(self):
            self.user = SimpleNamespace(
                id=user_id,
                tenant_id=tenant_id,
                is_active=True,
                roles=[],
            )

        def add(self, record):
            return None

        async def execute(self, statement):
            return _Result(self.user)

        async def flush(self):
            return None

    async def _emit(*args, **kwargs):
        return None

    monkeypatch.setattr(action_service, "get_action", lambda name: _Tool())
    monkeypatch.setattr(action_service, "emit_event", _emit)

    record = await action_service._handle_one(
        cast(AsyncSession, _Db()),
        call=SimpleNamespace(name="test_tool", arguments={}, rationale=""),
        context=ActionContext(
            tenant_id=tenant_id,
            user_id=user_id,
            decision_run_id=None,
        ),
    )

    assert record.status == "rejected"
    assert (
        "action:propose" in (record.rejection_reason or "")
        or "lacks" in (record.rejection_reason or "")
    )


@pytest.mark.asyncio
async def test_execute_approved_refuses_a_disabled_tool(monkeypatch):
    """A tool switched off after approval must not run on approval."""
    from app.services.actions import policy as policy_service
    from app.services.actions import service as action_service
    from app.services.actions.base import ActionResult

    class _Tool:
        name = "test_tool"
        risk_level = "report"
        parameters_model: Any

        executed = False

        async def execute(self, *, db, payload, context):
            type(self).executed = True
            return ActionResult(output={})

        async def verify(self, *, db, payload, context, result):
            raise AssertionError("must not verify")

    class _Params:
        @classmethod
        def model_validate(cls, value):
            return value

    _Tool.parameters_model = _Params

    record = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        decision_run_id=None,
        tool_name="test_tool",
        status="approved",
        arguments={},
        error_message=None,
        verification_result={},
    )

    events: list[dict] = []

    async def _emit(db, **kwargs):
        events.append(kwargs)

    async def _resolve(db, **kwargs):
        return policy_service.POLICY_DISABLED

    class _Db:
        async def flush(self):
            return None

    monkeypatch.setattr(action_service, "get_action", lambda name: _Tool())
    monkeypatch.setattr(action_service, "emit_event", _emit)
    monkeypatch.setattr(policy_service, "resolve", _resolve)

    result = await action_service.execute_approved(
        cast(AsyncSession, _Db()),
        record=cast(Any, record),
        actor_user_id=uuid.uuid4(),
    )

    assert _Tool.executed is False
    assert result.status == "failed"
    assert "disabled by tenant policy" in (result.error_message or "")
