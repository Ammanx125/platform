# app/services/workflows/registry.py
"""
Workflow registry.

Central map from workflow key -> Workflow definition. Populated at import
time by the templates package. No DB, no session, no side effects.
"""
from __future__ import annotations

from typing import Any

from app.services.workflows.base import (
    VALID_STEP_TYPES,
    Workflow,
)

_REGISTRY: dict[str, Workflow] = {}


def register(workflow: Any) -> None:
    """
    Register a workflow. Validates the definition; raises on structural
    problems so a bad workflow can't make it past import.
    """
    if not workflow.key:
        raise ValueError("workflow.key must be non-empty")
    if workflow.key in _REGISTRY:
        raise ValueError(f"duplicate workflow key: {workflow.key!r}")
    if not workflow.steps:
        raise ValueError(f"workflow {workflow.key!r}: must have at least one step")
    step_names = [s.name for s in workflow.steps]
    if len(step_names) != len(set(step_names)):
        raise ValueError(f"workflow {workflow.key!r}: duplicate step names")
    for s in workflow.steps:
        if s.type not in VALID_STEP_TYPES:
            raise ValueError(
                f"workflow {workflow.key!r} step {s.name!r}: "
                f"unknown type {s.type!r}; valid: {sorted(VALID_STEP_TYPES)}"
            )
    _REGISTRY[workflow.key] = workflow


def get(key: str) -> Workflow | None:
    return _REGISTRY.get(key)


def all_workflows() -> list[Workflow]:
    return list(_REGISTRY.values())


def keys() -> list[str]:
    return sorted(_REGISTRY.keys())


def match_by_query(query: str) -> list[str]:
    """
    Return workflow keys whose trigger keywords overlap with the query.

    Used by the orchestrator's planner rules tier. Does not rank; callers
    that need ranking should count overlapping tokens.
    """
    import re
    toks = set(re.findall(r"[a-z0-9]+", query.lower()))
    matched: list[str] = []
    for wf in _REGISTRY.values():
        if len(toks & wf.trigger_keywords) >= 2:
            matched.append(wf.key)
    return matched