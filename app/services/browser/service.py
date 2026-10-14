# app/services/browser/service.py
"""
Browser extraction service layer.

Stage 2 scope: authorization lifecycle only. Profiles and runs arrive in
later stages and will add their own functions to this module.

A BrowserAuthorization is the platform's record that the tenant approved
browser-based extraction for a specific source and origin. The platform
refuses to issue a browser_extract job when no active authorization
exists. That refusal lives in the job-issuing code (stage 4); stage 2
only manages the records.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.browser import BrowserAuthorization
from app.db.models.dataset import DataSource
from app.services.audit import service as audit_service
from app.services.audit import types as audit_types


class BrowserServiceError(Exception):
    """Raised for business-level failures the router maps to 4xx."""


async def _load_agent_source(
    db: AsyncSession, *, tenant_id: uuid.UUID, source_id: uuid.UUID
) -> DataSource:
    """
    Resolve a source and assert it is an agent source owned by the tenant.

    Browser extraction is scoped to on-prem agents. We do NOT allow it on
    csv/excel/sql/http/webhook sources even if a caller somehow constructs
    a request for them.
    """
    source = (
        await db.execute(
            select(DataSource).where(
                DataSource.id == source_id,
                DataSource.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if source is None:
        raise BrowserServiceError("source not found for tenant")
    if source.source_type != "agent":
        raise BrowserServiceError(
            f"browser extraction requires an 'agent' source, "
            f"got {source.source_type!r}"
        )
    return source


async def create_authorization(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    user_id: uuid.UUID,
    origin: str,
    approved_purpose: str,
    scope: dict,
    evidence_ref: str | None,
) -> BrowserAuthorization:
    """
    Record that the tenant has authorized browser extraction for a given
    source and origin.

    Idempotency: if an active authorization already exists for
    (tenant, source, origin), we return it unchanged. This makes the
    endpoint safe to retry without creating a duplicate active record.
    """
    source = await _load_agent_source(
        db, tenant_id=tenant_id, source_id=source_id
    )

    existing = (
        await db.execute(
            select(BrowserAuthorization).where(
                BrowserAuthorization.tenant_id == tenant_id,
                BrowserAuthorization.source_id == source.id,
                BrowserAuthorization.origin == origin,
                BrowserAuthorization.status == "active",
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    auth = BrowserAuthorization(
        tenant_id=tenant_id,
        source_id=source.id,
        authorized_by_user_id=user_id,
        origin=origin,
        approved_purpose=approved_purpose,
        scope=dict(scope or {}),
        evidence_ref=evidence_ref,
        status="active",
    )
    db.add(auth)
    await db.flush()

    await audit_service.emit(
        db,
        tenant_id=tenant_id,
        event_type=audit_types.BROWSER_AUTHORIZATION_CREATED,
        actor_user_id=user_id,
        subject_type="browser_authorization",
        subject_id=auth.id,
        metadata={
            "source_id": str(source.id),
            "origin": origin,
            "evidence_ref": evidence_ref,
        },
        message=f"browser authorization created for {origin}",
    )
    return auth


async def revoke_authorization(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    authorization_id: uuid.UUID,
    user_id: uuid.UUID,
) -> BrowserAuthorization:
    """
    Revoke an authorization. Rows are never deleted — they carry the audit
    trail of who approved access and when it was withdrawn.

    Idempotent: revoking an already-revoked authorization returns the
    existing row unchanged.
    """
    auth = (
        await db.execute(
            select(BrowserAuthorization)
            .where(
                BrowserAuthorization.id == authorization_id,
                BrowserAuthorization.tenant_id == tenant_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if auth is None:
        raise BrowserServiceError("authorization not found")

    if auth.status == "revoked":
        return auth

    auth.status = "revoked"
    auth.revoked_at = datetime.now(UTC)
    auth.revoked_by_user_id = user_id
    await db.flush()

    await audit_service.emit(
        db,
        tenant_id=tenant_id,
        event_type=audit_types.BROWSER_AUTHORIZATION_REVOKED,
        actor_user_id=user_id,
        subject_type="browser_authorization",
        subject_id=auth.id,
        metadata={
            "source_id": str(auth.source_id),
            "origin": auth.origin,
        },
        message=f"browser authorization revoked for {auth.origin}",
    )
    return auth


async def list_authorizations(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID | None = None,
    include_revoked: bool = False,
) -> list[BrowserAuthorization]:
    stmt = select(BrowserAuthorization).where(
        BrowserAuthorization.tenant_id == tenant_id,
    )
    if source_id is not None:
        stmt = stmt.where(BrowserAuthorization.source_id == source_id)
    if not include_revoked:
        stmt = stmt.where(BrowserAuthorization.status == "active")
    stmt = stmt.order_by(BrowserAuthorization.created_at.desc())
    return list((await db.execute(stmt)).scalars().all())


async def get_active_authorization(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    origin: str,
) -> BrowserAuthorization | None:
    """
    Used by the job-issuing path (stage 4) to gate extraction on an active
    authorization. Kept here so the query has exactly one home.
    """
    return (
        await db.execute(
            select(BrowserAuthorization).where(
                BrowserAuthorization.tenant_id == tenant_id,
                BrowserAuthorization.source_id == source_id,
                BrowserAuthorization.origin == origin,
                BrowserAuthorization.status == "active",
            )
        )
    ).scalar_one_or_none()