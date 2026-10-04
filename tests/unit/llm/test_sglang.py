# tests/unit/llm/test_sglang.py
"""
Exercises the SGLang provider against a small in-process HTTP server that
speaks the OpenAI-compatible wire format. No real model is involved.

Marked slow because it starts a server; opt-in.
"""
from __future__ import annotations

import json

import pytest
import pytest_asyncio
from aiohttp import web

from app.core.config import settings
from app.services.llm.base import LLMProviderError
from app.services.llm.gemini import GeminiProvider
from app.services.llm.sglang import SGLangProvider

pytestmark = pytest.mark.slow


async def _server(handler):
    app = web.Application()
    app.router.add_post("/v1/chat/completions", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    return runner, port


@pytest_asyncio.fixture
async def happy_server():
    async def handler(request):
        body = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps({
                            "summary": "Fine.",
                            "claims": [],
                            "recommended_actions": [],
                            "tool_calls": [],
                        })
                    }
                }
            ]
        }
        return web.json_response(body)

    runner, port = await _server(handler)
    yield port
    await runner.cleanup()


@pytest_asyncio.fixture
async def malformed_server():
    async def handler(request):
        body = {"choices": [{"message": {"content": "not json at all"}}]}
        return web.json_response(body)

    runner, port = await _server(handler)
    yield port
    await runner.cleanup()


@pytest.mark.asyncio
async def test_happy_path(happy_server):
    p = SGLangProvider(base_url=f"http://127.0.0.1:{happy_server}/v1")
    r = await p.generate(system="You are Sansa.", messages=[{"role": "user", "content": "hi"}])
    assert r.summary == "Fine."
    assert r.provider == "sglang"


@pytest.mark.asyncio
async def test_malformed_json_raises(malformed_server):
    p = SGLangProvider(base_url=f"http://127.0.0.1:{malformed_server}/v1")
    with pytest.raises(LLMProviderError, match="valid JSON"):
        await p.generate(system="s", messages=[])


@pytest.mark.asyncio
async def test_transient_http_status_is_retried():
    calls = 0

    async def handler(_request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return web.json_response(
                {"error": {"message": "temporarily unavailable"}},
                status=503,
            )
        return web.json_response({
            "choices": [
                {
                    "message": {
                        "content": json.dumps({
                            "summary": "Recovered.",
                            "claims": [],
                            "recommended_actions": [],
                            "tool_calls": [],
                        })
                    }
                }
            ]
        })

    runner, port = await _server(handler)
    provider = SGLangProvider(
        base_url=f"http://127.0.0.1:{port}/v1",
        max_retries=1,
    )

    try:
        response = await provider.generate(system="s", messages=[])
    finally:
        await runner.cleanup()

    assert response.summary == "Recovered."
    assert calls == 2


@pytest.mark.asyncio
async def test_gemini_uses_compatible_chat_endpoint(monkeypatch):
    async def handler(request):
        assert request.headers["Authorization"] == "Bearer test-gemini-key"
        payload = await request.json()
        assert payload["model"] == "gemini-test"
        assert payload["messages"][0]["role"] == "system"
        assert payload["response_format"] == {"type": "json_object"}
        return web.json_response({
            "choices": [
                {
                    "message": {
                        "content": json.dumps({
                            "summary": "Fine.",
                            "claims": [],
                            "recommended_actions": [],
                            "tool_calls": [],
                        })
                    }
                }
            ]
        })

    runner, port = await _server(handler)
    monkeypatch.setattr(
        settings, "gemini_base_url", f"http://127.0.0.1:{port}/v1"
    )
    monkeypatch.setattr(settings, "gemini_model_name", "gemini-test")
    monkeypatch.setattr(settings, "llm_api_key", "test-gemini-key")
    provider = GeminiProvider()

    try:
        response = await provider.generate(
            system="You are Sansa.",
            messages=[{"role": "user", "content": "hi"}],
        )
    finally:
        await runner.cleanup()

    assert response.summary == "Fine."
    assert response.provider == "gemini"
    assert response.model_name == "gemini-test"


@pytest.mark.asyncio
async def test_gemini_normalizes_native_tool_calls(monkeypatch):
    async def handler(_request):
        return web.json_response({
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {
                                    "name": "review_supplier",
                                    "arguments": '{"supplier":"Cocoa Coast"}',
                                },
                            }
                        ],
                    },
                }
            ]
        })

    runner, port = await _server(handler)
    monkeypatch.setattr(
        settings, "gemini_base_url", f"http://127.0.0.1:{port}/v1"
    )
    monkeypatch.setattr(settings, "gemini_model_name", "gemini-test")
    monkeypatch.setattr(settings, "llm_api_key", "test-gemini-key")
    provider = GeminiProvider()

    try:
        response = await provider.generate(
            system="You are Sansa.",
            messages=[{"role": "user", "content": "Review supplier risk."}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "review_supplier",
                        "description": "Review a supplier.",
                        "parameters": {
                            "type": "object",
                            "properties": {"supplier": {"type": "string"}},
                        },
                    },
                }
            ],
        )
    finally:
        await runner.cleanup()

    assert response.provider == "gemini"
    assert response.summary
    assert response.tool_calls[0].name == "review_supplier"
    assert response.tool_calls[0].arguments == {"supplier": "Cocoa Coast"}