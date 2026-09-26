# app/services/actions/registry.py
"""
Action registry.

Central map from tool name -> Action instance. Populated at import time by
the internal tool modules. No DB, no session, no side effects.
"""
from __future__ import annotations

from typing import Any

from app.services.actions.base import VALID_RISK_LEVELS, Action

_REGISTRY: dict[str, Action] = {}


def register(action: Any) -> None:
    """
    Register an action. Raises on duplicate name or invalid definition.

    Called at import time by each tool module. Validates that the action
    declares a well-formed risk level and a parameters model.
    """
    if not action.name:
        raise ValueError("action.name must be non-empty")
    if action.name in _REGISTRY:
        raise ValueError(f"duplicate action name: {action.name!r}")
    if action.risk_level not in VALID_RISK_LEVELS:
        raise ValueError(
            f"action {action.name!r}: unknown risk_level "
            f"{action.risk_level!r}; expected one of {sorted(VALID_RISK_LEVELS)}"
        )
    if action.parameters_model is None:
        raise ValueError(
            f"action {action.name!r}: parameters_model is required"
        )
    if not hasattr(action, "validators"):
        raise ValueError(
            f"action {action.name!r}: validators list is required"
        )
    _REGISTRY[action.name] = action


def get(name: str) -> Action | None:
    return _REGISTRY.get(name)


def all_actions() -> list[Action]:
    return list(_REGISTRY.values())


def names() -> list[str]:
    return sorted(_REGISTRY.keys())


def tool_schemas() -> list[dict]:
    """
    Return the OpenAI-style tool descriptors for every registered action.

    Used by the orchestrator to pass the tool registry to the LLM.
    """
    schemas: list[dict] = []
    for a in _REGISTRY.values():
        schema = a.parameters_model.model_json_schema()
        # Pydantic puts the top-level model's "title" in the schema; the
        # OpenAI tool format doesn't want it.
        schema.pop("title", None)
        schemas.append({
            "type": "function",
            "function": {
                "name": a.name,
                "description": a.description,
                "parameters": schema,
            },
        })
    return schemas