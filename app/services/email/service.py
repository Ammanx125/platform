# app/services/email/service.py
"""
Email account lifecycle: connect, list, disconnect, sync.

This module owns the *platform-side* email flows. Provider-specific I/O
lives in GmailProvider (or the fake). OAuth state lifecycle lives in
oauth_state.py. This module is the glue: create DataSource + EmailAccount,
drive the OAuth round-trip, and enqueue ingestion jobs.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.dataset import DataSource, IngestionJob
from app.db.models.email import EmailAccount
from app.services.email.base import (
    EmailAuthError,
    EmailProviderError,
)
from app.services.email.oauth_state import (
    OAuthStateError,
    consume_state,
    create_state,
    mark_failed,
)
from app.services.email.provider_registry import get_provider
from app.services.email.tokens import serialize_tokens


class EmailServiceError(Exception):
    """Raised for business-level failures the router maps to 4xx."""


async def start_connect(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    provider_name: str,
    name: str,
    source_id: uuid.UUID | None,
) -> tuple[uuid.UUID, str, datetime]:
    """
    Create (or reuse) an email DataSource, create a pending OAuth state
    row, and build the provider's authorize URL.

    Returns (source_id, authorize_url, expires_at). The caller redirects
    the browser to authorize_url.
    """
    provider = get_provider(provider_name)
    source_type = {
        "gmail": "email_gmail",
        "graph": "email_graph",
    }.get(provider_name)
    if source_type is None:
        raise EmailServiceError(f"unknown provider {provider_name!r}")

    if source_id is not None:
        source = (
            await db.execute(
                select(DataSource).where(
                    DataSource.id == source_id,
                    DataSource.tenant_id == tenant_id,
                    DataSource.source_type == source_type,
                )
            )
        ).scalar_one_or_none()
        if source is None:
            raise EmailServiceError("source not found for tenant/provider")
    else:
        source = (
            await db.execute(
                select(DataSource).where(
                    DataSource.tenant_id == tenant_id,
                    DataSource.name == name,
                )
            )
        ).scalar_one_or_none()
        if source is not None and source.source_type != source_type:
            raise EmailServiceError(
                f"source name {name!r} is already used by another source type"
            )
        if source is None:
            source = DataSource(
                tenant_id=tenant_id,
                name=name,
                source_type=source_type,
                is_active=True,
            )
            db.add(source)
            await db.flush()
        elif not source.is_active:
            source.is_active = True
            await db.flush()

    state = await create_state(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        source_id=source.id,
        provider=provider_name,
        redirect_uri=settings.email_oauth_redirect_uri,
        scopes=list(settings.google_oauth_scopes),
    )
    try:
        authorize_url = await provider.build_authorize_url(
            state=state.nonce,
            redirect_uri=settings.email_oauth_redirect_uri,
        )
    except EmailAuthError as exc:
        raise EmailServiceError(f"could not build authorize URL: {exc}") from exc

    return source.id, authorize_url, state.expires_at


async def complete_connect(
    db: AsyncSession,
    *,
    nonce: str,
    code: str,
    provider_name: str,
) -> EmailAccount:
    """
    Handle the OAuth callback. Consumes the state, exchanges the code,
    fetches the profile, and creates (or updates) the EmailAccount.
    """
    try:
        bound = await consume_state(db, nonce=nonce, provider=provider_name)
    except OAuthStateError as exc:
        raise EmailServiceError(f"invalid OAuth state: {exc}") from exc

    provider = get_provider(provider_name)

    try:
        tokens = await provider.exchange_code(
            code=code,
            redirect_uri=bound.redirect_uri,
        )
    except EmailAuthError as exc:
        await mark_failed(db, state_id=bound.state_id, error=str(exc))
        raise EmailServiceError(f"OAuth exchange failed: {exc}") from exc

    try:
        profile = await provider.get_profile(tokens=tokens)
    except EmailProviderError as exc:
        await mark_failed(db, state_id=bound.state_id, error=str(exc))
        raise EmailServiceError(f"could not read mailbox profile: {exc}") from exc

    email_address = (profile.get("email_address") or "").strip().lower()
    if not email_address:
        await mark_failed(db, state_id=bound.state_id, error="profile missing email")
        raise EmailServiceError("provider profile did not include an email address")

    # Idempotent reconnect: if an account already exists for this
    # (tenant, address), refresh its tokens rather than erroring.
    existing = (
        await db.execute(
            select(EmailAccount).where(
                EmailAccount.tenant_id == bound.tenant_id,
                EmailAccount.email_address == email_address,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        existing.source_id = bound.source_id
        existing.provider = provider_name
        existing.display_name = profile.get("display_name") or existing.display_name
        existing.status = "active"
        existing.oauth_tokens_encrypted = serialize_tokens(tokens)
        existing.last_error = None
        existing.connected_by_user_id = bound.user_id
        existing.account_metadata = {
            **(existing.account_metadata or {}),
            "granted_scopes": tokens.scope.split() if tokens.scope else [],
            "provider_profile": {
                k: v for k, v in profile.items()
                if k not in {"email_address", "display_name"}
            },
        }
        await db.flush()
        return existing

    account = EmailAccount(
        tenant_id=bound.tenant_id,
        source_id=bound.source_id,
        provider=provider_name,
        email_address=email_address,
        display_name=profile.get("display_name"),
        status="active",
        oauth_tokens_encrypted=serialize_tokens(tokens),
        connected_by_user_id=bound.user_id,
        account_metadata={
            "granted_scopes": tokens.scope.split() if tokens.scope else [],
            "provider_profile": {
                k: v for k, v in profile.items()
                if k not in {"email_address", "display_name"}
            },
        },
    )
    db.add(account)
    await db.flush()
    return account


async def list_accounts(
    db: AsyncSession, *, tenant_id: uuid.UUID
) -> list[EmailAccount]:
    rows = (
        await db.execute(
            select(EmailAccount)
            .where(EmailAccount.tenant_id == tenant_id)
            .order_by(EmailAccount.created_at.desc())
        )
    ).scalars().all()
    return list(rows)


async def get_account(
    db: AsyncSession, *, tenant_id: uuid.UUID, account_id: uuid.UUID
) -> EmailAccount | None:
    return (
        await db.execute(
            select(EmailAccount).where(
                EmailAccount.id == account_id,
                EmailAccount.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()


async def disconnect_account(
    db: AsyncSession, *, tenant_id: uuid.UUID, account_id: uuid.UUID
) -> EmailAccount:
    """
    Revoke tokens at the provider (best effort), then mark the account
    revoked. We do NOT delete the row: keeping it preserves the
    audit trail of when the connection existed.
    """
    account = await get_account(db, tenant_id=tenant_id, account_id=account_id)
    if account is None:
        raise EmailServiceError("account not found")
    if account.oauth_tokens_encrypted:
        try:
            from app.services.email.tokens import deserialize_tokens
            tokens = deserialize_tokens(account.oauth_tokens_encrypted)
            provider = get_provider(account.provider)
            await provider.revoke_tokens(tokens=tokens)
        except Exception:  # noqa: BLE001
            # Revocation is best-effort; expiring tokens bound the damage.
            pass
    account.status = "revoked"
    account.last_error = None
    await db.flush()
    return account


async def enqueue_sync(
    db: AsyncSession, *, tenant_id: uuid.UUID, account_id: uuid.UUID
) -> IngestionJob:
    """
    Enqueue a sync job for the account's DataSource. The worker picks it
    up and runs run_job -> the email branch.
    """
    account = await get_account(db, tenant_id=tenant_id, account_id=account_id)
    if account is None:
        raise EmailServiceError("account not found")
    if account.status == "revoked":
        raise EmailServiceError("account is revoked; reconnect first")

    source = (
        await db.execute(
            select(DataSource).where(
                DataSource.id == account.source_id,
                DataSource.tenant_id == tenant_id,
                DataSource.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if source is None:
        raise EmailServiceError("source not found or inactive")

    job = IngestionJob(
        tenant_id=tenant_id,
        source_id=source.id,
        status="pending",
    )
    db.add(job)
    await db.flush()
    return job


async def list_messages(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    account_id: uuid.UUID,
    limit: int = 50,
    intent: str | None = None,
    urgency: str | None = None,
) -> list[dict]:
    """
    List recent ingested email Documents for an account's source, newest
    first. Returns plain dicts shaped for EmailMessageRead.
    """
    from app.db.models.knowledge import Document

    account = await get_account(db, tenant_id=tenant_id, account_id=account_id)
    if account is None:
        raise EmailServiceError("account not found")

    stmt = (
        select(Document)
        .where(
            Document.tenant_id == tenant_id,
            Document.source_id == account.source_id,
            Document.content_type == "email",
            Document.status == "ready",
        )
        .order_by(Document.created_at.desc())
        .limit(max(1, min(limit, 200)))
    )
    docs = list((await db.execute(stmt)).scalars().all())

    rows: list[dict] = []
    for doc in docs:
        meta = doc.doc_metadata or {}
        if intent and meta.get("intent") != intent:
            continue
        if urgency and meta.get("urgency") != urgency:
            continue
        received_at = None
        raw = meta.get("received_at")
        if isinstance(raw, str):
            from datetime import datetime
            try:
                received_at = datetime.fromisoformat(raw)
            except ValueError:
                received_at = None
        rows.append({
            "document_id": doc.id,
            "message_id": meta.get("message_id"),
            "thread_id": meta.get("thread_id"),
            "subject": meta.get("subject") or doc.title,
            "from_address": meta.get("from_address"),
            "from_name": meta.get("from_name"),
            "received_at": received_at,
            "intent": meta.get("intent"),
            "urgency": meta.get("urgency"),
            "snippet": meta.get("snippet"),
        })
    return rows