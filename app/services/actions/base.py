# app/services/actions/base.py
"""
Action contracts.

An Action is a tool Sansa can propose and (subject to validation and, in
11b, approval) execute. Actions are declarative: they declare their schema,
their risk level, and their validators; the framework owns the pipeline.

Security boundary (same as the LLM provider in Step 9):

    Action defines WHAT it does and WHAT it requires.
    The framework validates, authorizes, and executes.
    The model NEVER calls an Action directly.

Validators are per-concern. A tool with multiple checks declares them all
in `validators`; the framework runs them in order and stops at the first
failure. Each validator is independently unit-testable.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol, runtime_checkable

from pydantic import BaseModel


@dataclass
class ActionContext:
    """
    Runtime context passed to validators and execute.

    Carries identity and origin, not business state. A validator that
    needs tenant data reads it from `db` (passed separately to execute,
    not to validators, so validators stay pure).
    """
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    decision_run_id: uuid.UUID | None
    source_ids: list[uuid.UUID] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class ValidationOutcome:
    """
    The result of one validator.

    passed: True if the check succeeded.
    error:  human-readable reason if failed; None otherwise.
    detail: structured data for the audit trail (thresholds checked, values
            compared, etc). Never user-facing.
    """
    passed: bool
    error: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class ActionResult:
    """
    The output of a successful execution.

    output:    the concrete result — a list of suppliers, a generated
               document id, etc. Shape depends on the tool.
    """
    output: dict[str, Any] = field(default_factory=dict)


@dataclass
class VerificationOutcome:
    """
    The result of post-action verification.

    verified: True if the side effect was confirmed to have happened.
    detail:   structured data for the audit trail (what was checked, what
              was found).
    error:    human-readable reason if verification failed; None otherwise.
    """
    verified: bool
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class ActionError(Exception):
    """Raised by execute() when the tool cannot complete."""


# A validator is a pure function: (payload, context) -> ValidationOutcome.
# It must not touch the DB. Anything needing DB access belongs in execute(),
# where the session is available.
Validator = Any  # `Callable[[BaseModel, ActionContext], ValidationOutcome]`


@runtime_checkable
class Action(Protocol):
    """
    The Action contract.

    Implementations declare:
      - name:        unique identifier used by the LLM and the registry
      - description: shown to the LLM as the tool description
      - risk_level:  one of "read" | "report" | "configurable" | "required" | "elevated"
      - parameters_model: Pydantic model; the JSON Schema sent to the LLM
                          is derived from it
      - validators:  class-level list of Validator functions
      - execute():   performs the operation against a DB session
            - verify():    confirms that a successful execute() actually had its
                                         effect. Called only after execute() returns without
                                         raising. Read-only tools typically return
                                         VerificationOutcome(verified=True) immediately.

        Validators run BEFORE execute. verify() runs AFTER execute.
    """
    name: ClassVar[str]
    description: ClassVar[str]
    risk_level: ClassVar[str]
    parameters_model: ClassVar[type[BaseModel]]
    validators: ClassVar[list[Validator]]

    async def execute(
        self,
        *,
        db: Any,                       # AsyncSession; typed Any to keep base.py free of SQLAlchemy
        payload: BaseModel,
        context: ActionContext,
    ) -> ActionResult: ...

    async def verify(
        self,
        *,
        db: Any,
        payload: BaseModel,
        context: ActionContext,
        result: ActionResult,
    ) -> VerificationOutcome: ...


# Risk levels. Used by 11b's approval gate; declared here so tools can
# reference them today.
RISK_READ = "read"
RISK_REPORT = "report"
RISK_CONFIGURABLE = "configurable"
RISK_REQUIRED = "required"
RISK_ELEVATED = "elevated"

VALID_RISK_LEVELS = frozenset({
    RISK_READ, RISK_REPORT, RISK_CONFIGURABLE, RISK_REQUIRED, RISK_ELEVATED,
})