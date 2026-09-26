# tests/unit/llm/test_mock.py
import pytest

from app.services.llm.mock import MockLLMProvider
from app.services.llm.schemas import LLMResponse, ToolCall


@pytest.mark.asyncio
async def test_default_response_is_fixed():
    p = MockLLMProvider()
    a = await p.generate(system="s", messages=[{"role": "user", "content": "hi"}])
    b = await p.generate(system="different", messages=[{"role": "user", "content": "bye"}])
    assert a.summary == b.summary == "Mock response"
    assert a.claims == b.claims == []
    assert a.tool_calls == b.tool_calls == []
    assert a.provider == "mock"


@pytest.mark.asyncio
async def test_injected_response_is_returned():
    injected = LLMResponse(
        summary="Injected.",
        tool_calls=[ToolCall(name="frobnicate", arguments={"x": 1})],
    )
    p = MockLLMProvider(response=injected)
    r = await p.generate(system="s", messages=[])
    assert r.summary == "Injected."
    assert r.tool_calls[0].name == "frobnicate"
    # provider/model_name defaulted to the mock's identity
    assert r.provider == "mock"
    assert r.model_name == "mock"


@pytest.mark.asyncio
async def test_ignores_system_and_tools():
    """The mock doesn't care about its inputs; that's the point."""
    p = MockLLMProvider()
    r1 = await p.generate(system="a", messages=[], tools=[{"type": "function"}])
    r2 = await p.generate(system="b", messages=[{"role": "user", "content": "..."}])
    assert r1.summary == r2.summary