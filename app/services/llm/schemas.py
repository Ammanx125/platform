# app/services/llm/schemas.py
"""
Canonical Sansa LLM contracts.

Every provider returns an LLMResponse. The shape is fixed; the fields are
the same regardless of which model server produced it.

Tool calls are proposals. The provider guarantees they are structurally
well-formed ({name: str, arguments: dict}); it does NOT guarantee the tool
exists or that the caller is authorized to invoke it. Those checks belong
to the orchestrator.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    """
    A model-proposed tool invocation.

    name:       identifier the model believes corresponds to a tool. May be
                unknown to Sansa; the orchestrator will reject unknown names.
    arguments:  the arguments the model supplied. Shape is unconstrained at
                this layer; the tool's own schema validates them downstream.
    rationale:  optional one-line justification. Not chain-of-thought —
                a summary of *why* the model proposes this call.
    """
    name: str
    arguments: dict[str, Any]
    rationale: str | None = None


@dataclass
class Claim:
    """
    A factual statement the model is making, with the evidence it cites.

    Step 10 will validate claims against the evidence package before
    presenting them to the user. Claims that don't line up with retrieved
    data are rejected. The shape is deliberately simple: a claim either
    cites a specific piece of evidence (by id, from the evidence package)
    or it's unsupported and will be flagged.
    """
    text: str
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class RecommendedAction:
    """
    A model-suggested next step, distinct from a tool call.

    A tool call is "execute this now." A recommendation is "consider doing
    this." The orchestrator turns recommendations into tool calls only
    after human intent (approval flow, Step 12) or explicit user request.
    """
    description: str
    suggested_tool: str | None = None
    rationale: str | None = None


@dataclass
class LLMResponse:
    """
    The canonical response from any LLM provider.

    summary:              short natural-language answer to the user
    claims:               factual statements with evidence references
    recommended_actions:  suggestions, not executions
    tool_calls:           structured proposals awaiting orchestrator
                          validation and (when required) approval
    provider:             "mock" | "sglang" | ... — for audit
    model_name:           the concrete model, e.g. "gemma-4-26b-a4b-it"
    raw:                  the provider's native response, for debugging
    """
    summary: str
    claims: list[Claim] = field(default_factory=list)
    recommended_actions: list[RecommendedAction] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    provider: str = ""
    model_name: str = ""
    raw: Any | None = None


@dataclass
class ToolSchema:
    """
    A tool the caller is offering to the model.

    Exactly JSON Schema for parameters. This is what every model server and
    tool-calling framework understands; passing it through without
    translation means SGLang can forward it directly to Gemma.

    The provider does not validate that `parameters` is a complete or
    correct schema. It validates only the top-level shape of the tool entry
    (name: str, description: str, parameters: dict).
    """
    name: str
    description: str
    parameters: dict[str, Any]

    def to_wire(self) -> dict[str, Any]:
        """Serialize to the OpenAI-style tool descriptor used by SGLang."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }