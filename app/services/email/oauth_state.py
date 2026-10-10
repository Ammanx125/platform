# app/services/email/oauth_state.py
"""
OAuth state nonce lifecycle.

The state param exists to prevent CSRF on the OAuth callback. Without it,
an attacker can start an authorization to *their* mailbox on their own
Sansa account and then trick a victim into completing the callback —
linking the victim's Sansa session to the attacker's mailbox, or vice
versa. See RFC 6749 §10.12.

create_state():  called by an authenticated endpoint. Generates a random
                 nonce, stores a hash, returns the plaintext nonce.
consume_state(): called by the unauthenticated callback. Hashes the
                 incoming nonce, finds the matching row, rejects if
                 expired/consumed/failed, marks it consumed, returns it.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.email import EmailOAuthState


class OAuthStateError(Exception):
    """State nonce invalid, expired, or already used."""


@dataclass(frozen=True)
class CreatedState:
    nonce: str
    expires_at: datetime


@dataclass(frozen=True)
class ConsumedState:
    state_id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    source_id: uuid.UUID
    provider: str
    redirect_uri: str
    scopes: list[str]


_NONCE_BYTES = 32  # 256 bits; state is not a password but we don't skimp.


def _hash(nonce: str) -> str:
    return hashlib.sha256(nonce.encode("utf-8")).hexdigest()


async def create_state(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    source_id: uuid.UUID,
    provider: str,
    redirect_uri: str,
    scopes: list[str],
) -> CreatedState:
    nonce = secrets.token_urlsafe(_NONCE_BYTES)
    expires_at = datetime.now(UTC) + timedelta(
        minutes=settings.email_oauth_state_ttl_minutes
    )
    row = EmailOAuthState(
        tenant_id=tenant_id,
        user_id=user_id,
        source_id=source_id,
        provider=provider,
        state_hash=_hash(nonce),
        redirect_uri=redirect_uri,
        scopes=list(scopes),
        status="pending",
        expires_at=expires_at,
    )
    db.add(row)
    await db.flush()
    return CreatedState(nonce=nonce, expires_at=expires_at)


async def consume_state(
    db: AsyncSession,
    *,
    nonce: str,
    provider: str,
) -> ConsumedState:
    """
    One-shot: validates, marks consumed, and returns the bound metadata.

    Rejects when:
      - no row matches the hash
      - row belongs to a different provider (cross-provider nonce reuse)
      - row.status is not 'pending' (already consumed/failed/expired)
      - row.expires_at < now
    """
    if not nonce:
        raise OAuthStateError("missing state parameter")

    state_hash = _hash(nonce)
    row = (
        await db.execute(
            select(EmailOAuthState)
            .where(EmailOAuthState.state_hash == state_hash)
            .with_for_update()
        )
    ).scalar_one_or_none()

    if row is None:
        raise OAuthStateError("unknown or already-consumed state")
    if row.provider != provider:
        raise OAuthStateError("state was issued for a different provider")
    if row.status != "pending":
        raise OAuthStateError(f"state is {row.status}, not pending")
    if row.expires_at < datetime.now(UTC):
        row.status = "expired"
        await db.flush()
        raise OAuthStateError("state expired")

    row.status = "consumed"
    row.consumed_at = datetime.now(UTC)
    await db.flush()

    return ConsumedState(
        state_id=row.id,
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        source_id=row.source_id,
        provider=row.provider,
        redirect_uri=row.redirect_uri,
        scopes=list(row.scopes),
    )


async def mark_failed(
    db: AsyncSession, *, state_id: uuid.UUID, error: str
) -> None:
    """
    Called by the callback when token exchange fails after consume_state
    has run. Leaves an audit trail so we can see which OAuth attempts
    failed and why.
    """
    row = (
        await db.execute(
            select(EmailOAuthState).where(EmailOAuthState.id == state_id)
        )
    ).scalar_one_or_none()
    if row is None:
        return
    row.status = "failed"
    row.error = error[:2000]
    await db.flush()