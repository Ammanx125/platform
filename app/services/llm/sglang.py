# app/services/llm/sglang.py
"""
SGLang provider: HTTP client for an SGLang-served Gemma model.

SGLang exposes an OpenAI-compatible /v1/chat/completions endpoint. This
provider speaks that protocol and parses the response into an LLMResponse.

What this provider does NOT do (and must never do):
  - Look up tool names in Sansa's registry
  - Check whether the caller is authorized for a tool
  - Apply business rules to proposed actions
  - Attempt to override the model's output based on policy

Those belong to the orchestrator (Step 10). This class is a wire adapter.

The model is asked to return JSON matching the Sansa response schema. If
the model doesn't comply, the response fails structural validation and we
raise LLMProviderError. There is no best-effort salvage of malformed
output — silently accepting garbage is how prompt-injection payloads get
through.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings
from app.services.llm.base import LLMProviderError
from app.services.llm.schemas import LLMResponse
from app.services.llm.validation import validate_response

# The system prompt is composed by the caller. This provider only guarantees
# that the model is instructed to emit valid JSON in the Sansa response
# shape. The caller supplies the domain policy via `system`.
_RESPONSE_FORMAT_INSTRUCTION = (
    "Respond with a single JSON object containing exactly these fields:\n"
    '  "summary": string (required)\n'
    '  "claims": array of {"text": string, "evidence_ids": [string]}\n'
    '  "recommended_actions": array of {"description": string, '
    '"suggested_tool": string|null, "rationale": string|null}\n'
    '  "tool_calls": array of {"name": string, "arguments": object, '
    '"rationale": string|null}\n'
    "No prose outside the JSON object."
)


class SGLangProvider:
    name = "sglang"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model_name: str | None = None,
        api_key: str | None = None,
        timeout_seconds: int | None = None,
        max_retries: int | None = None,
    ) -> None:
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.model_name = model_name or settings.model_name
        self.api_key = api_key if api_key is not None else settings.llm_api_key
        self.timeout = timeout_seconds or settings.llm_request_timeout_seconds
        self.max_retries = max_retries if max_retries is not None else settings.llm_max_retries

    async def generate(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        # Compose the request.
        combined_system = f"{system}\n\n{_RESPONSE_FORMAT_INSTRUCTION}"
        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": combined_system},
                *messages,
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        headers = {"Content-Type": "application/json"}
        if self.api_key and self.api_key != "INVALID_LOCAL_KEY":
            headers["Authorization"] = f"Bearer {self.api_key}"

        url = f"{self.base_url}/chat/completions"

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self.max_retries + 1),
            wait=wait_exponential(multiplier=0.5, min=0.5, max=5),
            retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
            reraise=True,
        ):
            with attempt:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(url, json=payload, headers=headers)

        if response.status_code >= 400:
            raise LLMProviderError(
                f"sglang returned HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )

        try:
            wire = response.json()
        except json.JSONDecodeError as exc:
            raise LLMProviderError(
                f"sglang response was not JSON: {response.text[:200]}"
            ) from exc

        content = self._extract_content(wire)
        parsed = self._parse_content_json(content)

        return validate_response(
            parsed,
            provider=self.name,
            model_name=self.model_name,
        )

    def _extract_content(self, wire: dict[str, Any]) -> str:
        """Pull the assistant message text out of the OpenAI-format response."""
        try:
            choices = wire["choices"]
            if not choices:
                raise KeyError("choices empty")
            message = choices[0]["message"]
            content = message.get("content")
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMProviderError(
                f"unexpected response shape from sglang: {wire!r}"
            ) from exc

        if not isinstance(content, str):
            raise LLMProviderError(
                f"expected string content, got {type(content).__name__}"
            )
        return content

    def _parse_content_json(self, content: str) -> dict[str, Any]:
        """
        Parse the assistant content as JSON.

        We do not attempt to strip markdown fences or repair truncation.
        A model that emits code-fenced JSON (```json ... ```) is
        non-compliant with the response_format instruction; accepting it
        would teach us to accept non-JSON output from less careful models.
        """
        text = content.strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMProviderError(
                f"model did not return valid JSON: {text[:200]}"
            ) from exc
        if not isinstance(parsed, dict):
            raise LLMProviderError(
                f"model returned JSON {type(parsed).__name__}, expected object"
            )
        return parsed