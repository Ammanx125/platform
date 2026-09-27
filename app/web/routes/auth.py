# app/web/routes/auth.py
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_csrf
from app.api.v1.auth import _clear_auth_cookies, _set_auth_cookies
from app.core.exceptions import AuthError
from app.db.session import get_db
from app.services.auth import service as auth_service
from app.web.templating import base_context, templates

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    return templates.TemplateResponse(
        request, "login.html", {**base_context(), "error": None}
    )


@router.post("/login")
async def login_submit(
    request: Request,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    try:
        _user, access, refresh = await auth_service.login(
            db, email=email, password=password
        )
    except AuthError as exc:
        return templates.TemplateResponse(
            request,
            "login.html",
            {**base_context(), "error": str(exc)},
            status_code=401,
        )

    response = RedirectResponse(url="/", status_code=303)
    _set_auth_cookies(response, access=access, refresh=refresh)
    return response


@router.post("/logout", dependencies=[Depends(require_csrf)])
async def logout(request: Request, db: Annotated[AsyncSession, Depends(get_db)]):
    refresh_cookie = request.cookies.get("sansa_refresh")
    if refresh_cookie:
        try:
            await auth_service.logout(db, raw_refresh_token=refresh_cookie)
        except Exception:
            pass
    response = RedirectResponse(url="/login", status_code=303)
    _clear_auth_cookies(response)
    return response