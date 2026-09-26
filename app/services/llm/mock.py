# app/services/llm/mock.py
"""
Deterministic, non-semantic mock provider.

Default behavior: returns a fixed valid LLMResponse regardless of input.
Tests inject specific LLMResponse instances to exercise tool calls,
malformed provider output, and orchestration behavior.

The mock does NOT attempt to emulate model reasoning. Making it "smart"
(a rule-based mock) would mean integration tests exercise the mock's rules
instead of Sansa's orchestration — which is exactly what we want to avoid.
"""
from __future__ import annotations

from app.services.llm.schemas import LLMResponse


class MockLLMProvider:
    name = "mock"
    model_name = "mock"

    def __init__(self, *, response: LLMResponse | None = None) -> None:
        """
        response: if provided, every call returns this exact response
                  (with provider/model_name overridden to match the mock
                  unless the caller already set them). If None, a fixed
                  default response is returned.
        """
        self._response = response

    def _default_response(self) -> LLMResponse:
        return LLMResponse(
            summary="Mock response",
            claims=[],
            recommended_actions=[],
            tool_calls=[],
            provider=self.name,
            model_name=self.model_name,
            raw=None,
        )

    async def generate(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        if self._response is not None:
            # Return a shallow copy so callers can't mutate the injected
            # response across calls. Deep copy would be safer but the
            # structure is simple and tests expect identity of the top-level
            # fields to be stable.
            r = self._response
            return LLMResponse(
                summary=r.summary,
                claims=list(r.claims),
                recommended_actions=list(r.recommended_actions),
                tool_calls=list(r.tool_calls),
                provider=r.provider or self.name,
                model_name=r.model_name or self.model_name,
                raw=r.raw,
            )
        return self._default_response()