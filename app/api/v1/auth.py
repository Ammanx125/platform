# app/api/v1/auth.py
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, require_csrf
from app.core.config import settings
from app.core.exceptions import AuthError
from app.core.security import generate_csrf_token
from app.db.session import get_db
from app.schemas.auth import LoginRequest, MeResponse
from app.services.auth import service as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_auth_cookies(response: Response, *, access: str, refresh: str) -> None:
    secure = settings.cookie_secure
    samesite = settings.cookie_samesite
    domain = settings.cookie_domain

    response.set_cookie(
        key=settings.access_cookie_name,
        value=access,
        max_age=settings.access_token_minutes * 60,
        httponly=True,
        secure=secure,
        samesite=samesite,
        domain=domain,
        path="/",
    )
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=refresh,
        max_age=settings.refresh_token_days * 24 * 60 * 60,
        httponly=True,
        secure=secure,
        samesite=samesite,
        domain=domain,
        path="/",
    )
    # CSRF cookie: readable by JS (double-submit pattern), NOT HttpOnly.
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=generate_csrf_token(),
        max_age=settings.refresh_token_days * 24 * 60 * 60,
        httponly=False,
        secure=secure,
        samesite=samesite,
        domain=domain,
        path="/",
    )


def _clear_auth_cookies(response: Response) -> None:
    for name in (
        settings.access_cookie_name,
        settings.refresh_cookie_name,
        settings.csrf_cookie_name,
    ):
        response.delete_cookie(key=name, path="/", domain=settings.cookie_domain)


@router.post("/login")
async def login(
    body: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, str]:
    try:
        _user, access, refresh = await auth_service.login(
            db, email=body.email, password=body.password
        )
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc

    _set_auth_cookies(response, access=access, refresh=refresh)
    return {"status": "ok"}


@router.post("/refresh", dependencies=[Depends(require_csrf)])
async def refresh_session(
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    refresh_cookie: Annotated[str | None, Cookie(alias=settings.refresh_cookie_name)] = None,
) -> dict[str, str]:
    if not refresh_cookie:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="no refresh token",
        )

    try:
        _user, access, new_refresh = await auth_service.refresh(
            db, raw_refresh_token=refresh_cookie
        )
    except AuthError as exc:
        _clear_auth_cookies(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc

    _set_auth_cookies(response, access=access, refresh=new_refresh)
    return {"status": "ok"}


@router.post("/logout", dependencies=[Depends(require_csrf)])
async def logout(
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    refresh_cookie: Annotated[str | None, Cookie(alias=settings.refresh_cookie_name)] = None,
) -> dict[str, str]:
    if refresh_cookie:
        await auth_service.logout(db, raw_refresh_token=refresh_cookie)
    _clear_auth_cookies(response)
    return {"status": "ok"}


@router.get("/me", response_model=MeResponse)
async def me(user: CurrentUser) -> MeResponse:
    role_names = [r.name for r in user.roles]
    permissions = sorted({p.key for r in user.roles for p in r.permissions})
    return MeResponse(
        id=user.id,
        tenant_id=user.tenant_id,
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
        roles=role_names,
        permissions=permissions,
    )