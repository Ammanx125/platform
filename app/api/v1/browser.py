# app/api/v1/browser.py
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentTenantId, require_permission
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.browser import (
    BrowserAuthorizationCreate,
    BrowserAuthorizationRead,
)
from app.services.browser import service as browser_service
from app.services.browser.service import BrowserServiceError

router = APIRouter(prefix="/browser", tags=["browser"])


@router.post(
    "/sources/{source_id}/authorization",
    response_model=BrowserAuthorizationRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_authorization(
    source_id: uuid.UUID,
    body: BrowserAuthorizationCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    user: Annotated[User, Depends(require_permission("dataset:write"))],
) -> BrowserAuthorizationRead:
    """
    Record that the tenant authorizes browser extraction from a specific
    origin, for a specific agent source. Required before any extraction
    profile may be scheduled.
    """
    try:
        auth = await browser_service.create_authorization(
            db,
            tenant_id=tenant_id,
            source_id=source_id,
            user_id=user.id,
            origin=body.origin,
            approved_purpose=body.approved_purpose,
            scope=body.scope,
            evidence_ref=body.evidence_ref,
        )
    except BrowserServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(auth)
    return BrowserAuthorizationRead.model_validate(auth)


@router.get(
    "/sources/{source_id}/authorizations",
    response_model=list[BrowserAuthorizationRead],
)
async def list_authorizations(
    source_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("dataset:read"))],
    include_revoked: bool = Query(default=False),
) -> list[BrowserAuthorizationRead]:
    rows = await browser_service.list_authorizations(
        db,
        tenant_id=tenant_id,
        source_id=source_id,
        include_revoked=include_revoked,
    )
    return [BrowserAuthorizationRead.model_validate(r) for r in rows]


@router.delete(
    "/authorizations/{authorization_id}",
    response_model=BrowserAuthorizationRead,
)
async def revoke_authorization(
    authorization_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    user: Annotated[User, Depends(require_permission("dataset:write"))],
) -> BrowserAuthorizationRead:
    """
    Revoke an authorization. Does not delete the record; the audit trail
    is preserved, and historical runs continue to reference it.
    """
    try:
        auth = await browser_service.revoke_authorization(
            db,
            tenant_id=tenant_id,
            authorization_id=authorization_id,
            user_id=user.id,
        )
    except BrowserServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(auth)
    return BrowserAuthorizationRead.model_validate(auth)