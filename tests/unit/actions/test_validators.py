# tests/unit/actions/test_validators.py
from pydantic import BaseModel

from app.services.actions.base import ActionContext, ValidationOutcome
from app.services.actions.validators import run_validators


class _Payload(BaseModel):
    value: str = "test"


def _ctx() -> ActionContext:
    import uuid
    return ActionContext(
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        decision_run_id=None,
    )


def test_run_validators_stops_at_first_failure():
    calls = []

    def ok(payload, ctx):
        calls.append("ok")
        return ValidationOutcome(passed=True)

    def bad(payload, ctx):
        calls.append("bad")
        return ValidationOutcome(passed=False, error="nope")

    def never(payload, ctx):
        calls.append("never")
        return ValidationOutcome(passed=True)

    results = run_validators([ok, bad, never], _Payload(), _ctx())
    assert calls == ["ok", "bad"]
    assert results[-1][1].error == "nope"


def test_run_validators_catches_exceptions():
    def boom(payload, ctx):
        raise RuntimeError("kaboom")

    results = run_validators([boom], _Payload(), _ctx())
    assert not results[0][1].passed
    assert "kaboom" in (results[0][1].error or "")