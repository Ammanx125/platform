# app/main.py
from __future__ import annotations

from pathlib import Path
from typing import cast

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.responses import Response

from app.api.v1 import api_router
from app.core.config import settings
from app.core.rate_limit import limiter
from app.db.session import get_db
from app.web.deps import _RedirectToLogin, make_redirect_to_login
from app.web.router import web_router

_FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"

app = FastAPI(title=settings.app_name)


def _rate_limit_exception_handler(request: Request, exc: Exception) -> Response:
    return _rate_limit_exceeded_handler(request, cast(RateLimitExceeded, exc))


app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exception_handler)
app.add_middleware(SlowAPIMiddleware)


@app.exception_handler(_RedirectToLogin)
async def _redirect_handler(request: Request, exc: _RedirectToLogin):
    return make_redirect_to_login()


app.mount(
    "/static",
    StaticFiles(directory=str(_FRONTEND_DIR / "static")),
    name="static",
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