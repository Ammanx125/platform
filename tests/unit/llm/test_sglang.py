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

from app.services.llm.base import LLMProviderError
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