# app/api/v1/email.py
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentTenantId, require_permission
from app.core.config import settings
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.email import (
    EmailAccountRead,
    EmailConnectRequest,
    EmailConnectResponse,
    EmailMessageRead,
    EmailSyncResponse,
)
from app.services.email import service as email_service
from app.services.email.service import EmailServiceError

router = APIRouter(prefix="/email", tags=["email"])


# ---------- authenticated ----------

@router.post(
    "/connect",
    response_model=EmailConnectResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_connect(
    body: EmailConnectRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    user: Annotated[User, Depends(require_permission("dataset:write"))],
) -> EmailConnectResponse:
    """
    Begin an OAuth connection. Returns the authorize URL the caller must
    redirect the browser to. The state nonce inside the URL is one-shot
    and tied to this tenant/user pair.
    """
    try:
        source_id, url, expires_at = await email_service.start_connect(
            db,
            tenant_id=tenant_id,
            user_id=user.id,
            provider_name=body.provider,
            name=body.name,
            source_id=body.source_id,
        )
    except EmailServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.commit()
    return EmailConnectResponse(
        source_id=source_id,
        authorize_url=url,
        expires_at=expires_at,
    )


@router.get("/accounts", response_model=list[EmailAccountRead])
async def list_accounts(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("dataset:read"))],
) -> list[EmailAccountRead]:
    accounts = await email_service.list_accounts(db, tenant_id=tenant_id)
    return [EmailAccountRead.model_validate(a) for a in accounts]


@router.delete(
    "/accounts/{account_id}",
    response_model=EmailAccountRead,
)
async def disconnect_account(
    account_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("dataset:write"))],
) -> EmailAccountRead:
    try:
        account = await email_service.disconnect_account(
            db, tenant_id=tenant_id, account_id=account_id
        )
    except EmailServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(account)
    return EmailAccountRead.model_validate(account)


@router.post(
    "/accounts/{account_id}/sync",
    response_model=EmailSyncResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def enqueue_sync(
    account_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("dataset:write"))],
) -> EmailSyncResponse:
    try:
        job = await email_service.enqueue_sync(
            db, tenant_id=tenant_id, account_id=account_id
        )
    except EmailServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.commit()
    return EmailSyncResponse(ingestion_job_id=job.id, status=job.status)


@router.get(
    "/accounts/{account_id}/messages",
    response_model=list[EmailMessageRead],
)
async def list_messages(
    account_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("dataset:read"))],
    limit: int = Query(default=50, ge=1, le=200),
    intent: str | None = Query(default=None),
    urgency: str | None = Query(default=None),
) -> list[EmailMessageRead]:
    try:
        rows = await email_service.list_messages(
            db,
            tenant_id=tenant_id,
            account_id=account_id,
            limit=limit,
            intent=intent,
            urgency=urgency,
        )
    except EmailServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [EmailMessageRead(**r) for r in rows]


# ---------- unauthenticated (Google redirect target) ----------

@router.get("/oauth/callback")
async def oauth_callback(
    db: Annotated[AsyncSession, Depends(get_db)],
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    provider: str = "gmail",
):
    """
    Google redirects the browser here after consent. NOT authenticated —
    the caller is Google, not a Sansa session. The state nonce is what
    binds this callback to the original tenant/user.

    On failure we redirect to a static error page rather than returning
    JSON; the browser is the caller.
    """
    if error:
        return RedirectResponse(
            url="/email/connect/error?reason=provider_error",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if not code or not state:
        return RedirectResponse(
            url="/email/connect/error?reason=missing_params",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    try:
        account = await email_service.complete_connect(
            db, nonce=state, code=code, provider_name=provider
        )
    except EmailServiceError:
        await db.rollback()
        return RedirectResponse(
            url="/email/connect/error?reason=exchange_failed",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    await db.commit()
    return RedirectResponse(
        url=f"/email/accounts/{account.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )