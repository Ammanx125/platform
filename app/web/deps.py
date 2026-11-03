# app/web/deps.py
"""
HTML-flavored dependencies.

The API layer raises 401 for unauthenticated requests; HTML routes should
redirect to /login instead. This module provides the redirecting variants.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.models.user import User
from app.db.session import get_db


async def require_html_user(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """
    Return the current user or raise a redirect to /login.

    Raising an HTTPException with a Location header isn't quite right;
    FastAPI will turn it into a JSON error. Instead we raise a custom
    exception caught by a handler in main.py that redirects.
    """
    try:
        return await get_current_user(db=db, access_token=request.cookies.get(
            "sansa_access"
        ))
    except HTTPException:
        raise _RedirectToLogin() from None


class _RedirectToLogin(Exception):
    """Sentinel caught by the exception handler in app/main.py."""


def make_redirect_to_login() -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=303)


HtmlUser = Annotated[User, Depends(require_html_user)]


class _PermissionDenied(Exception):
    """Carries the permission name to the handler in app/main.py."""

    def __init__(self, permission: str) -> None:
        self.permission = permission
        super().__init__(permission)


def require_html_permission(required: str):
    """
    HTML counterpart of app.api.deps.require_permission.

    Authentication on HTML routes is a cookie + redirect flow, so the API's
    JSON 403 is wrong here: it would leave a signed-in user staring at a
    JSON body. This raises a sentinel that main.py turns into a rendered
    page. The permission check itself is identical — any of the user's
    roles granting `required` is enough.

    Never rely on the template hiding a button. Hiding is presentation; this
    is the enforcement point.
    """

    async def _checker(user: HtmlUser) -> User:
        granted = {p.key for role in user.roles for p in role.permissions}
        if required not in granted:
            raise _PermissionDenied(required)
        return user

    return _checker