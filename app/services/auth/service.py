# app/services/auth/service.py
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AuthError, TenantIsolationError
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_refresh_token,
    verify_password,
)
from app.db.models.refresh_token import RefreshToken
from app.db.models.tenant import Tenant
from app.db.models.user import User


async def _load_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def _issue_refresh_token(
    db: AsyncSession,
    *,
    user: User,
    family_id: uuid.UUID,
) -> tuple[str, RefreshToken]:
    raw = generate_refresh_token()
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_days)
    row = RefreshToken(
        tenant_id=user.tenant_id,
        user_id=user.id,
        token_hash=hash_refresh_token(raw),
        family_id=family_id,
        expires_at=expires_at,
    )
    db.add(row)
    await db.flush()
    return raw, row


async def login(db: AsyncSession, *, email: str, password: str) -> tuple[User, str, str]:
    """Returns (user, access_token, raw_refresh_token)."""
    user = await _load_user_by_email(db, email)
    if user is None or not verify_password(password, user.password_hash):
        # Same error for "no such user" and "wrong password" — no enumeration.
        raise AuthError("invalid credentials")
    if not user.is_active:
        raise AuthError("user is disabled")

    tenant = (
        await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))
    ).scalar_one_or_none()
    if tenant is None or not tenant.is_active:
        raise AuthError("tenant is disabled")

    access = create_access_token(user_id=user.id, tenant_id=user.tenant_id)
    family_id = uuid.uuid4()
    raw_refresh, _ = await _issue_refresh_token(db, user=user, family_id=family_id)
    await db.commit()
    return user, access, raw_refresh


async def refresh(db: AsyncSession, *, raw_refresh_token: str) -> tuple[User, str, str]:
    """
    Rotate a refresh token. Reuse of a revoked token revokes the whole family.
    Returns (user, new_access_token, new_raw_refresh_token).
    """
    token_hash = hash_refresh_token(raw_refresh_token)
    row = (
        await db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
    ).scalar_one_or_none()

    if row is None:
        raise AuthError("invalid refresh token")

    now = datetime.now(timezone.utc)

    if row.revoked_at is not None:
        # Reuse of a revoked token: revoke the entire family, refuse.
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == row.family_id)
            .values(revoked_at=now)
        )
        await db.commit()
        raise AuthError("refresh token reuse detected")

    if row.expires_at <= now:
        raise AuthError("refresh token expired")

    user = (
        await db.execute(select(User).where(User.id == row.user_id))
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        raise AuthError("user is disabled")

    # Guard against any tampering: row.tenant_id must match user.tenant_id.
    if row.tenant_id != user.tenant_id:
        raise TenantIsolationError("refresh token tenant mismatch")

    # Revoke current, issue new in same family.
    new_raw, new_row = await _issue_refresh_token(db, user=user, family_id=row.family_id)
    row.revoked_at = now
    row.replaced_by_id = new_row.id
    await db.commit()

    access = create_access_token(user_id=user.id, tenant_id=user.tenant_id)
    return user, access, new_raw


async def logout(db: AsyncSession, *, raw_refresh_token: str) -> None:
    """Revoke a single refresh token (best-effort; idempotent)."""
    token_hash = hash_refresh_token(raw_refresh_token)
    row = (
        await db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
    ).scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        return
    row.revoked_at = datetime.now(timezone.utc)
    await db.commit()