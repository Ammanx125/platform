# app/services/workflows/registry.py
"""
Workflow registry.

Central map from workflow key -> Workflow definition. Populated at import
time by the templates package. No DB, no session, no side effects.
"""
from __future__ import annotations

import re
from typing import Any

from app.services.workflows.base import (
    VALID_STEP_TYPES,
    Workflow,
)

_REGISTRY: dict[str, Workflow] = {}


def _match_score(workflow: Workflow, query: str, tokens: set[str]) -> int:
    score = 0
    for keyword in workflow.trigger_keywords:
        if " " in keyword:
            if keyword in query:
                score += len(keyword.split())
        elif keyword in tokens:
            score += 1
    return score


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
    Return workflow keys whose trigger keywords match the query, best match first.

    Domain-specific trigger words are sufficient to route an explicit
    business question (e.g. "How are operations performing?"). Multi-word
    triggers are matched as phrases; single-word triggers are matched as
    query tokens.
    """
    normalized_query = query.lower()
    toks = set(re.findall(r"[a-z0-9]+", normalized_query))
    matched: list[tuple[int, str]] = []
    for wf in _REGISTRY.values():
        score = _match_score(wf, normalized_query, toks)
        if score:
            matched.append((score, wf.key))
    matched.sort(key=lambda item: (-item[0], item[1]))
    return [key for _, key in matched]


def match_score(query: str, workflow_key: str) -> int:
    """Return the number of trigger terms matched for a registered workflow."""

    workflow = _REGISTRY.get(workflow_key)
    if workflow is None:
        return 0

    normalized_query = query.lower()
    toks = set(re.findall(r"[a-z0-9]+", normalized_query))
    return _match_score(workflow, normalized_query, toks)