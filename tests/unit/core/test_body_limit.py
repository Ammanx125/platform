# tests/unit/core/test_body_limit.py
"""
The body limit must be a real memory bound, not a post-hoc refusal.

The earlier check ran after `await request.body()` had buffered the payload,
so a 2 GB upload was a 2 GB allocation before it was rejected.
"""

import pytest

from app.core.body_limit import BodyLimitMiddleware


async def _drain(app, *, path="/api/v1/webhooks/x", headers=None, chunks=None):
    """Drive the middleware with a fake ASGI app that reads the whole body."""
    chunks = chunks or []
    scope = {"type": "http", "path": path, "headers": headers or []}
    pending = list(chunks)
    consumed = {"bytes": 0}

    async def receive():
        if pending:
            body = pending.pop(0)
            consumed["bytes"] += len(body)
            return {"type": "http.request", "body": body, "more_body": bool(pending)}
        return {"type": "http.request", "body": b"", "more_body": False}

    sent = []

    async def send(message):
        sent.append(message)

    async def inner(scope, receive, send):
        # Mimics a handler: keeps pulling until the body is complete.
        while True:
            message = await receive()
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = BodyLimitMiddleware(
        inner, default_limit=1024, path_limits=[("/api/v1/webhooks/", 100)]
    )
    await middleware(scope, receive, send)
    return sent, consumed


@pytest.mark.asyncio
async def test_declared_content_length_is_rejected_before_reading():
    sent, consumed = await _drain(
        None, headers=[(b"content-length", b"50000")], chunks=[b"x" * 10]
    )

    assert sent[0]["status"] == 413
    # Nothing was read from the client.
    assert consumed["bytes"] == 0


@pytest.mark.asyncio
async def test_chunked_body_is_cut_off_at_the_limit():
    """No Content-Length: the byte counter is what stops it."""
    sent, consumed = await _drain(
        None, chunks=[b"x" * 60, b"x" * 60, b"x" * 60]
    )

    assert sent[0]["status"] == 413
    # Reading stopped as soon as the cap was crossed: the third chunk was
    # never pulled into application memory.
    assert consumed["bytes"] == 120


@pytest.mark.asyncio
async def test_body_within_the_limit_passes():
    sent, _ = await _drain(None, chunks=[b"x" * 50])

    assert sent[0]["status"] == 200


@pytest.mark.asyncio
async def test_longest_prefix_wins():
    async def unused_app(scope, receive, send):
        pass

    middleware = BodyLimitMiddleware(
        unused_app,
        default_limit=10,
        path_limits=[("/api/v1/", 100), ("/api/v1/webhooks/", 5)],
    )

    assert middleware._limit_for("/api/v1/webhooks/x") == 5
    assert middleware._limit_for("/api/v1/other") == 100
    assert middleware._limit_for("/elsewhere") == 10


def test_misconfigured_limit_fails_at_startup():
    async def unused_app(scope, receive, send):
        pass

    with pytest.raises(ValueError):
        BodyLimitMiddleware(unused_app, default_limit=0)
    with pytest.raises(ValueError):
        BodyLimitMiddleware(unused_app, default_limit=10, path_limits=[("/x", -1)])
