# app/services/llm/validation.py
"""
Structural validation of provider output.

Every provider parses its wire format, then calls validate_response() before
returning. The validator guarantees the LLMResponse shape; it does NOT
validate semantics (does the tool exist, is the action authorized). Those
belong to the orchestrator.

This module deliberately does not import the tool registry. It has no way
to know which tool names are legitimate.
"""
from __future__ import annotations

from typing import Any

from app.services.llm.base import LLMProviderError
from app.services.llm.schemas import (
    Claim,
    LLMResponse,
    RecommendedAction,
    ToolCall,
)


def _require(value: Any, *, field: str, types: tuple[type, ...]) -> None:
    if not isinstance(value, types):
        raise LLMProviderError(
            f"field {field!r} must be {[t.__name__ for t in types]}, "
            f"got {type(value).__name__}"
        )


def _validate_tool_call(raw: Any, index: int) -> ToolCall:
    if not isinstance(raw, dict):
        raise LLMProviderError(
            f"tool_calls[{index}] must be an object, got {type(raw).__name__}"
        )
    name = raw.get("name")
    arguments = raw.get("arguments")
    rationale = raw.get("rationale")

    _require(name, field=f"tool_calls[{index}].name", types=(str,))
    if not name:
        raise LLMProviderError(f"tool_calls[{index}].name must be non-empty")
    _require(arguments, field=f"tool_calls[{index}].arguments", types=(dict,))
    assert isinstance(arguments, dict)
    if rationale is not None:
        _require(rationale, field=f"tool_calls[{index}].rationale", types=(str,))

    return ToolCall(name=name, arguments=arguments, rationale=rationale)


def _validate_claim(raw: Any, index: int) -> Claim:
    if not isinstance(raw, dict):
        raise LLMProviderError(
            f"claims[{index}] must be an object, got {type(raw).__name__}"
        )
    text = raw.get("text")
    evidence_ids = raw.get("evidence_ids", [])

    _require(text, field=f"claims[{index}].text", types=(str,))
    assert isinstance(text, str)
    if not isinstance(evidence_ids, list):
        raise LLMProviderError(
            f"claims[{index}].evidence_ids must be a list, "
            f"got {type(evidence_ids).__name__}"
        )
    for i, eid in enumerate(evidence_ids):
        if not isinstance(eid, str):
            raise LLMProviderError(
                f"claims[{index}].evidence_ids[{i}] must be a string"
            )
    return Claim(text=text, evidence_ids=list(evidence_ids))


def _validate_recommendation(raw: Any, index: int) -> RecommendedAction:
    if not isinstance(raw, dict):
        raise LLMProviderError(
            f"recommended_actions[{index}] must be an object, "
            f"got {type(raw).__name__}"
        )
    description = raw.get("description")
    suggested_tool = raw.get("suggested_tool")
    rationale = raw.get("rationale")

    _require(description, field=f"recommended_actions[{index}].description", types=(str,))
    assert isinstance(description, str)
    if suggested_tool is not None:
        _require(
            suggested_tool,
            field=f"recommended_actions[{index}].suggested_tool",
            types=(str,),
        )
    if rationale is not None:
        _require(rationale, field=f"recommended_actions[{index}].rationale", types=(str,))

    return RecommendedAction(
        description=description,
        suggested_tool=suggested_tool,
        rationale=rationale,
    )


def validate_response(
    raw: Any,
    *,
    provider: str,
    model_name: str,
) -> LLMResponse:
    """
    Take a provider's parsed (but not yet validated) output and produce a
    structurally valid LLMResponse.

    Raises LLMProviderError on any structural problem. Missing optional
    fields are filled with empty defaults.
    """
    if not isinstance(raw, dict):
        raise LLMProviderError(
            f"response must be an object, got {type(raw).__name__}"
        )

    summary = raw.get("summary")
    _require(summary, field="summary", types=(str,))
    assert isinstance(summary, str)

    raw_claims = raw.get("claims", []) or []
    raw_actions = raw.get("recommended_actions", []) or []
    raw_tool_calls = raw.get("tool_calls", []) or []

    if not isinstance(raw_claims, list):
        raise LLMProviderError("claims must be a list")
    if not isinstance(raw_actions, list):
        raise LLMProviderError("recommended_actions must be a list")
    if not isinstance(raw_tool_calls, list):
        raise LLMProviderError("tool_calls must be a list")

    claims = [_validate_claim(c, i) for i, c in enumerate(raw_claims)]
    actions = [_validate_recommendation(a, i) for i, a in enumerate(raw_actions)]
    tool_calls = [_validate_tool_call(t, i) for i, t in enumerate(raw_tool_calls)]

    return LLMResponse(
        summary=summary,
        claims=claims,
        recommended_actions=actions,
        tool_calls=tool_calls,
        provider=provider,
        model_name=model_name,
        raw=raw,
    )