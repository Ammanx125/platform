"""Gemini API adapter using Google's OpenAI-compatible chat endpoint."""
from __future__ import annotations

import json
from typing import Any

from app.core.config import settings
from app.services.llm.base import LLMProviderError
from app.services.llm.sglang import SGLangProvider


class GeminiProvider(SGLangProvider):
    """Use Google's OpenAI-compatible endpoint and normalize native tool calls."""

    name = "gemini"

    def __init__(self) -> None:
        super().__init__(
            base_url=settings.gemini_base_url,
            model_name=settings.gemini_model_name,
        )

    def _extract_content(self, wire: dict[str, Any]) -> str:
        try:
            choices = wire["choices"]
            if not choices:
                raise KeyError("choices empty")
            message = choices[0]["message"]
            content = message.get("content")
            tool_calls = message.get("tool_calls") or []
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMProviderError(
                f"unexpected response shape from {self.name}: {wire!r}"
            ) from exc

        if content is not None and not isinstance(content, str):
            raise LLMProviderError(
                f"expected string content, got {type(content).__name__}"
            )
        if not isinstance(tool_calls, list):
            raise LLMProviderError("expected tool_calls to be a list")
        if content is None and not tool_calls:
            raise LLMProviderError(
                f"{self.name} returned neither message content nor tool calls"
            )
        if not tool_calls:
            assert isinstance(content, str)
            return content

        parsed: dict[str, Any] = {}
        if content:
            try:
                parsed_content = json.loads(content)
            except json.JSONDecodeError as exc:
                raise LLMProviderError(
                    f"{self.name} returned non-JSON content alongside tool calls"
                ) from exc
            if not isinstance(parsed_content, dict):
                raise LLMProviderError(
                    f"{self.name} returned non-object JSON alongside tool calls"
                )
            parsed = parsed_content

        canonical_calls = []
        for index, call in enumerate(tool_calls):
            try:
                function = call["function"]
                name = function["name"]
                arguments = function["arguments"]
            except (KeyError, TypeError) as exc:
                raise LLMProviderError(
                    f"{self.name} tool_calls[{index}] has an invalid function shape"
                ) from exc
            if not isinstance(name, str) or not name:
                raise LLMProviderError(
                    f"{self.name} tool_calls[{index}].function.name must be non-empty"
                )
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError as exc:
                    raise LLMProviderError(
                        f"{self.name} tool_calls[{index}] arguments were not valid JSON"
                    ) from exc
            if not isinstance(arguments, dict):
                raise LLMProviderError(
                    f"{self.name} tool_calls[{index}] arguments must be an object"
                )
            canonical_calls.append({
                "name": name,
                "arguments": arguments,
                "rationale": None,
            })

        existing_calls = parsed.get("tool_calls", [])
        if not isinstance(existing_calls, list):
            raise LLMProviderError(
                f"{self.name} JSON field 'tool_calls' must be a list"
            )
        parsed["tool_calls"] = [*existing_calls, *canonical_calls]
        if not isinstance(parsed.get("summary"), str):
            parsed["summary"] = "I've prepared a suggested action for review."
        return json.dumps(parsed)
