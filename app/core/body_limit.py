# app/core/body_limit.py
"""
Request body limits enforced at the ASGI layer.

A check inside a route runs too late: FastAPI's `await request.body()` has
already buffered the whole payload, so a size test there refuses the request
after paying the memory cost. Middleware sees `http.request` chunks as they
arrive, so the limit is a real bound — reading stops at the ceiling and the
connection is refused.

Two signals are used, in order of cost:

  1. `Content-Length`, when present, is rejected before a byte is read.
     A client can lie about it or omit it, so it is never the only check.
  2. The accumulated chunk size, which is authoritative: the body is
     consumed through a wrapper that raises as soon as the cap is passed.

Limits are per path prefix, so an upload endpoint and a webhook can differ.
A request with no matching prefix uses the global ceiling.
"""

from __future__ import annotations

from collections.abc import Iterable

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class BodyTooLarge(Exception):
    """The request body exceeded its configured limit."""


class BodyLimitMiddleware:
    """
    Reject requests whose body exceeds a per-prefix limit.

    Misconfigured entries are rejected at construction so a typo'd limit
    fails at startup rather than silently not applying.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        default_limit: int,
        path_limits: Iterable[tuple[str, int]] = (),
    ) -> None:
        if default_limit <= 0:
            raise ValueError("default_limit must be positive")
        self.app = app
        self.default_limit = default_limit
        self.path_limits: list[tuple[str, int]] = []
        for prefix, limit in path_limits:
            if limit <= 0:
                raise ValueError(f"limit for {prefix!r} must be positive")
            self.path_limits.append((prefix, limit))
        # Longest prefix wins, so /api/v1/datasets/upload beats /api/v1.
        self.path_limits.sort(key=lambda item: len(item[0]), reverse=True)

    def _limit_for(self, path: str) -> int:
        for prefix, limit in self.path_limits:
            if path.startswith(prefix):
                return limit
        return self.default_limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = self._limit_for(scope.get("path", ""))

        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    # Unparseable length: fall through to the byte counter.
                    break
                if declared > limit:
                    await _reject(
                        send, limit, detail="declared Content-Length too large"
                    )
                    return
                break

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise BodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except BodyTooLarge:
            await _reject(send, limit, detail="body exceeded the limit while reading")


async def _reject(send: Send, limit: int, *, detail: str) -> None:
    body = (
        '{"detail":"' + f"request body too large (max {limit} bytes)" + '"}'
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"connection", b"close"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
