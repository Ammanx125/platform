from __future__ import annotations

import hmac

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import settings


class CookieCSRFMiddleware:
    """Require the configured double-submit token for cookie-authenticated API writes."""

    _SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
    _EXEMPT_PATHS = frozenset({"/api/v1/auth/login"})

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        path = request.url.path
        method = request.method.upper()
        if (
            not path.startswith("/api/v1/")
            or method in self._SAFE_METHODS
            or path in self._EXEMPT_PATHS
            or path.startswith("/api/v1/webhooks/")
            or self._is_bearer_agent_request(request)
            or not self._has_auth_cookie(request)
        ):
            await self.app(scope, receive, send)
            return

        csrf_cookie = request.cookies.get(settings.csrf_cookie_name)
        csrf_header = request.headers.get(settings.csrf_header_name)
        if (
            not csrf_cookie
            or not csrf_header
            or not hmac.compare_digest(csrf_cookie, csrf_header)
        ):
            response = JSONResponse(
                {"detail": "csrf check failed"},
                status_code=403,
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)

    @staticmethod
    def _has_auth_cookie(request: Request) -> bool:
        return any(
            name in request.cookies
            for name in (settings.access_cookie_name, settings.refresh_cookie_name)
        )

    @staticmethod
    def _is_bearer_agent_request(request: Request) -> bool:
        if not request.headers.get("authorization", "").lower().startswith("bearer "):
            return False

        parts = request.url.path.strip("/").split("/")
        if len(parts) < 5 or parts[:3] != ["api", "v1", "agents"]:
            return False
        if len(parts) == 5 and parts[4] in {"heartbeat", "sync"}:
            return True
        if len(parts) == 6 and parts[4] == "upload":
            return True
        return (
            len(parts) == 7
            and parts[4] == "jobs"
            and parts[6] == "result"
        )
