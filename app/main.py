# app/main.py
from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from app.api.v1 import api_router
from app.core.body_limit import BodyLimitMiddleware
from app.core.config import settings
from app.core.rate_limit import limiter
from app.db.session import get_db
from app.web.deps import (
    _PermissionDenied,
    _RedirectToLogin,
    make_redirect_to_login,
)
from app.web.router import web_router
from app.web.templating import templates

_FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"

@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
    if settings.environment.lower() in {"prod", "production"}:
        if not settings.redis_url:
            raise RuntimeError("REDIS_URL is required in production")
        redis = Redis.from_url(settings.redis_url)
        try:
            await redis.ping()
        except Exception as exc:
            raise RuntimeError("Redis rate-limit backend is unavailable") from exc
        finally:
            await redis.aclose()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)


def _rate_limit_exception_handler(request: Request, exc: Exception) -> Response:
    return _rate_limit_exceeded_handler(request, cast(RateLimitExceeded, exc))


app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exception_handler)
app.add_middleware(SlowAPIMiddleware)

# Body limits. Registered last so it wraps everything above it and sees a
# request before any handler (or rate-limiter storage) has buffered a body.
#
#   uploads    — one file per request, the largest legitimate payload
#   webhooks   — third-party callers, much smaller than an upload
#   default    — everything else
app.add_middleware(
    BodyLimitMiddleware,
    default_limit=settings.max_pdf_upload_bytes,
    path_limits=[
        ("/api/v1/webhooks/", settings.max_webhook_body_bytes),
        ("/api/v1/agents/", settings.agent_max_upload_bytes),
        ("/api/v1/datasets/", settings.max_pdf_upload_bytes),
        ("/ask", settings.max_form_body_bytes),
    ],
)


@app.exception_handler(_RedirectToLogin)
async def _redirect_handler(request: Request, exc: _RedirectToLogin):
    return make_redirect_to_login()


@app.exception_handler(_PermissionDenied)
async def _permission_denied_handler(request: Request, exc: _PermissionDenied):
    """A signed-in user without the required permission gets a page, not JSON."""
    return templates.TemplateResponse(
        request,
        "errors/forbidden.html",
        {
            "request": request,
            "permission": exc.permission,
            "app_name": settings.app_name,
            "csrf_cookie_name": settings.csrf_cookie_name,
            "csrf_header_name": settings.csrf_header_name,
        },
        status_code=403,
    )


app.mount(
    "/static",
    StaticFiles(directory=str(_FRONTEND_DIR / "static")),
    name="static",
)
app.mount(
    "/assets",
    StaticFiles(directory=str(_FRONTEND_DIR / "assets")),
    name="assets",
)

app.include_router(api_router)
app.include_router(web_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "app": settings.app_name}


@app.get("/health/db")
async def health_db(db: AsyncSession = Depends(get_db)):  # noqa: B008
    try:
        result = await db.execute(text("SELECT 1"))
        result.scalar_one()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ok", "database": "reachable"}