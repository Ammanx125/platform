# app/services/actions/validators.py
"""
Shared validator functions and validator-composition helpers.

Individual tools declare their own per-concern validators (validate_supplier
for the supplier tool, validate_quantity for the PO tool, etc). This module
holds validators that are genuinely cross-cutting, plus the runner that
applies a validator list.
"""
from __future__ import annotations

from pydantic import BaseModel

from app.services.actions.base import (
    ActionContext,
    ValidationOutcome,
    Validator,
)


def run_validators(
    validators: list[Validator],
    payload: BaseModel,
    context: ActionContext,
) -> list[tuple[str, ValidationOutcome]]:
    """
    Run each validator in order. Returns [(validator_name, outcome)] for
    every validator that ran, including the first failure.

    Stops at the first failure — later validators are not run. This is
    deliberate: later checks may assume earlier ones passed, and we don't
    want a cascading failure message.
    """
    results: list[tuple[str, ValidationOutcome]] = []
    for v in validators:
        name = getattr(v, "__name__", str(v))
        try:
            outcome = v(payload, context)
        except Exception as exc:  # noqa: BLE001
            outcome = ValidationOutcome(
                passed=False,
                error=f"validator {name} raised: {exc}",
            )
        results.append((name, outcome))
        if not outcome.passed:
            break
    return results


# ---------- cross-cutting validators ----------

def validate_evidence_cited(
    payload: BaseModel,
    context: ActionContext,
) -> ValidationOutcome:
    """
    A tool that produces a durable artifact (report, record, etc) must cite
    at least one piece of evidence. This is a generic policy: don't let the
    model trigger a write with nothing backing it.
    """
    if not context.evidence_ids:
        return ValidationOutcome(
            passed=False,
            error="action requires at least one cited evidence id",
            detail={"evidence_ids": []},
        )
    return ValidationOutcome(
        passed=True,
        detail={"evidence_count": len(context.evidence_ids)},
    )