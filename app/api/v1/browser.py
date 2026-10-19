# app/api/v1/browser.py
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentTenantId, require_permission
from app.db.models.user import User
from app.db.session import get_db
from app.services.browser import service as browser_service
from app.services.browser.service import BrowserServiceError
from app.schemas.browser import (
    BrowserAuthorizationCreate,
    BrowserAuthorizationRead,
    BrowserExtractionProfileCreate,
    BrowserExtractionProfileRead,
    BrowserExtractionRunRead,
    ScheduleExtractionResponse,
)

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

# ---------- profiles ----------

@router.post(
    "/sources/{source_id}/profile",
    response_model=BrowserExtractionProfileRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_profile(
    source_id: uuid.UUID,
    body: BrowserExtractionProfileCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    user: Annotated[User, Depends(require_permission("dataset:write"))],
) -> BrowserExtractionProfileRead:
    try:
        prof = await browser_service.create_profile(
            db,
            tenant_id=tenant_id,
            source_id=source_id,
            user_id=user.id,
            payload=body,
        )
    except BrowserServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(prof)
    return BrowserExtractionProfileRead.model_validate(prof)


@router.get(
    "/sources/{source_id}/profiles",
    response_model=list[BrowserExtractionProfileRead],
)
async def list_profiles(
    source_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("dataset:read"))],
    include_archived: bool = Query(default=False),
) -> list[BrowserExtractionProfileRead]:
    rows = await browser_service.list_profiles(
        db, tenant_id=tenant_id, source_id=source_id,
        include_archived=include_archived,
    )
    return [BrowserExtractionProfileRead.model_validate(r) for r in rows]


@router.patch(
    "/profiles/{profile_id}/status",
    response_model=BrowserExtractionProfileRead,
)
async def set_profile_status(
    profile_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    user: Annotated[User, Depends(require_permission("dataset:write"))],
    status_value: Annotated[str, Query(alias="status")],
) -> BrowserExtractionProfileRead:
    try:
        prof = await browser_service.set_profile_status(
            db, tenant_id=tenant_id, profile_id=profile_id,
            user_id=user.id, status=status_value,
        )
    except BrowserServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(prof)
    return BrowserExtractionProfileRead.model_validate(prof)


# ---------- scheduling ----------

@router.post(
    "/profiles/{profile_id}/extract",
    response_model=ScheduleExtractionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def schedule_extraction(
    profile_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    user: Annotated[User, Depends(require_permission("dataset:write"))],
) -> ScheduleExtractionResponse:
    try:
        _job, run = await browser_service.schedule_extraction(
            db, tenant_id=tenant_id, profile_id=profile_id, user_id=user.id,
        )
    except BrowserServiceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(run)
    return ScheduleExtractionResponse(
        run=BrowserExtractionRunRead.model_validate(run),
        agent_job_id=run.agent_job_id,
    )


@router.get(
    "/runs",
    response_model=list[BrowserExtractionRunRead],
)
async def list_runs(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("dataset:read"))],
    profile_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[BrowserExtractionRunRead]:
    rows = await browser_service.list_runs(
        db, tenant_id=tenant_id, profile_id=profile_id, limit=limit,
    )
    return [BrowserExtractionRunRead.model_validate(r) for r in rows]


@router.get(
    "/runs/{run_id}",
    response_model=BrowserExtractionRunRead,
)
async def get_run(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("dataset:read"))],
) -> BrowserExtractionRunRead:
    run = await browser_service.get_run(db, tenant_id=tenant_id, run_id=run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return BrowserExtractionRunRead.model_validate(run)