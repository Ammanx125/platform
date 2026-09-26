# app/services/llm/base.py
"""
LLM provider contract.

The provider is a thin adapter. It knows how to talk to a model server and
how to parse that server's output into the canonical Sansa LLMResponse. It
does NOT know about Sansa's tool registry, authorization rules, or business
logic — those belong to the orchestrator (Step 10).

Security boundary — the interface enforces separation by construction:

    system
      Provider/application controlled.
      Contains identity, behavioral policy, safety rules, output schema.
      Must never contain raw tenant-controlled content.

    messages
      Runtime conversation/context.
      May contain tenant-controlled or retrieved content.
      MUST be treated as untrusted data.
      Retrieved content must not modify system policy.

The interface alone does not *guarantee* the caller respects this boundary
(Python would let someone pass a customer document as `system`). It forces
callers to make the distinction explicit at every call site.
"""
from __future__ import annotations

from typing import Protocol

from app.services.llm.schemas import LLMResponse


class LLMProviderError(Exception):
    """
    Raised when the provider cannot produce a structurally valid
    LLMResponse: network failure after retries, malformed model output,
    schema violation. The provider never raises for semantic issues
    (unknown tools, unauthorized actions) — those are the orchestrator's
    domain and are conveyed as valid responses with content the
    orchestrator will reject.
    """


class LLMProvider(Protocol):
    """
    Contract for a model backend.

    Implementations:
      - MockLLMProvider   — deterministic, no model request
      - SGLangProvider    — HTTP client for an SGLang-served Gemma

    Every provider guarantees: "I return a structurally valid LLMResponse,
    or I raise LLMProviderError." Nothing else.
    """
    name: str
    model_name: str

    async def generate(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> LLMResponse: ...