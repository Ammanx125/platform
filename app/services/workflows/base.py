# app/services/workflows/base.py
"""
Workflow contracts.

A workflow is a stateful, domain-oriented process with defined steps and
persisted state. It orchestrates analyzers, capabilities, and actions;
it is not itself any of those things.

Two gates, independent, both must pass:

  - Workflow-level approval: an `await_approval` step pauses the instance
    until a human approves the plan.

  - Action-level approval: when a step calls handle_proposals, actions
    that require approval go to pending_approval via the action system.
    Workflow approval does NOT satisfy action approval. The action system
    is the single authority for whether an action executes.

Step types:
  - analyze     — runs a capability (retrieval/kpi/anomaly/forecast)
  - run_action  — calls handle_proposals with one or more tool calls
  - await_approval — pauses for workflow-level approval
  - await_action   — pauses until a specific action reaches terminal status
  - set_context    — writes to the workflow context bag (rarely needed)

A step type is a function: (ctx, step) -> StepResult.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol


@dataclass
class WorkflowContext:
    """
    Runtime context for one workflow execution.

    tenant_id, user_id, decision_run_id: identity (all forwarded to actions).
    source_ids: optional source scoping for evidence gathering.
    bag: the workflow_context JSONB, read and written by steps.
    """
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    decision_run_id: uuid.UUID | None
    source_ids: list[uuid.UUID] = field(default_factory=list)
    bag: dict[str, Any] = field(default_factory=dict)


@dataclass
class Step:
    """
    One step in a workflow definition.

    name:   unique within the workflow
    type:   "analyze" | "run_action" | "await_approval" | "await_action" | "set_context"
    params: type-specific parameters. See each handler in engine.py.

    Common params:
      analyze:     capability, capability_params
      run_action:  tool_calls (list of {name, arguments, rationale}), wait_for_approval (bool, default False), on_action_rejection ("fail"|"continue", default "fail")
      await_approval: prompt (str, shown to the approver)
      await_action:  action_id_from (JSON path into bag), on_rejection ("fail"|"continue", default "fail")
      set_context:   updates (dict, merged into bag)
    """
    name: str
    type: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepResult:
    """
    The result of running one step.

    status:  "completed" | "waiting" | "failed"
    output:  structured result for the step (recorded in step_states)
    error:   human-readable reason for failure or pause-with-reason
    wait_reason: when status=="waiting", what we're waiting on
                 ("workflow_approval" | "action_approval" | "input")
    wait_ref:    an id or key identifying the thing we're waiting on
                 (action_id for action_approval, step name for workflow_approval)
    """
    status: str
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    wait_reason: str | None = None
    wait_ref: str | None = None


@dataclass
class WorkflowRequirement:
    """
    A declared input the workflow needs to run well.

    kind:  "concept" | "kpi" | "detector" | "tool"
    key:   the identifier (concept key, KPI key, detector key, tool name)
    optional: if True, missing → skip silently; if False, missing → the
              workflow still runs, but the absence is recorded in the
              instance's trigger_metadata.availability_report.
    """
    kind: str
    key: str
    optional: bool = True


class Workflow(Protocol):
    """
    The Workflow contract.

    Implementations declare:
      - key:          unique identifier
      - display_name: human label
      - description:  shown in the API
      - domain:       "procurement" | "inventory" | ... 
      - steps:        ordered list of Step definitions
      - requirements: declared inputs (optional; empty list is valid)
      - trigger_keywords: tokens that suggest this workflow when matched
                          against a user query (used by the planner's
                          rules tier)

    run logic lives in the engine; workflows only declare their shape.
    """
    key: ClassVar[str]
    display_name: ClassVar[str]
    description: ClassVar[str]
    domain: ClassVar[str]
    steps: ClassVar[list[Step]]
    requirements: ClassVar[list[WorkflowRequirement]]
    trigger_keywords: ClassVar[frozenset[str]]


# Valid step types and statuses — used by the registry to validate.
VALID_STEP_TYPES = frozenset({
    "analyze", "run_action", "await_approval", "await_action", "set_context",
})

VALID_STEP_STATUSES = frozenset({
    "pending", "running", "completed", "waiting", "failed", "skipped",
})

VALID_INSTANCE_STATUSES = frozenset({
    "pending", "running",
    "waiting_workflow_approval", "waiting_action_approval", "waiting_input",
    "completed", "rejected", "failed", "cancelled",
})