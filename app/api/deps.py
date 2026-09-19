# app/api/deps.py
from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AuthError
from app.core.security import decode_access_token
from app.db.models.user import User
from app.db.session import get_db


async def get_current_user(
    db: Annotated[AsyncSession, Depends(get_db)],
    access_token: Annotated[str | None, Cookie(alias=settings.access_cookie_name)] = None,
) -> User:
    if not access_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")

    try:
        payload = decode_access_token(access_token)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    try:
        user_id = uuid.UUID(payload["sub"])
        token_tenant_id = uuid.UUID(payload["tid"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token") from exc

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid principal")

    # The token's tenant must match the user's actual tenant. If not, someone
    # tampered with a signed token or the user was moved — either way, refuse.
    if user.tenant_id != token_tenant_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid tenant")

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_current_tenant_id(user: CurrentUser) -> uuid.UUID:
    """Always derived from the authenticated user. Never from request input."""
    return user.tenant_id


CurrentTenantId = Annotated[uuid.UUID, Depends(get_current_tenant_id)]


def require_permission(required: str) -> Callable[..., object]:
    """
    Returns a dependency that asserts the current user has `required`.
    A user has the permission if any of their roles has it.
    """
    async def _checker(user: CurrentUser) -> User:
        # Roles are loaded via selectin on the User model. Each role has
        # its permissions loaded via selectin on the Role model.
        granted = {p.key for role in user.roles for p in role.permissions}
        if required not in granted:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"missing permission: {required}",
            )
        return user

    return _checker


def require_csrf(
    csrf_cookie: Annotated[str | None, Cookie(alias=settings.csrf_cookie_name)] = None,
    csrf_header: Annotated[str | None, Header(alias=settings.csrf_header_name)] = None,
) -> None:
    """Double-submit CSRF check. Both must be present and equal."""
    if not csrf_cookie or not csrf_header or csrf_cookie != csrf_header:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="csrf check failed")