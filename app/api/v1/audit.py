from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentTenantId, require_permission
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.audit import AuditEventRead
from app.services.audit import service as audit_service

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=list[AuditEventRead])
async def list_audit_events(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("audit:read"))],
    event_type: str | None = None,
    since: datetime | None = None,
    before: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[AuditEventRead]:
    events = await audit_service.list_events(
        db,
        tenant_id=tenant_id,
        event_type=event_type,
        since=since,
        before=before,
        limit=limit,
    )
    return [AuditEventRead.model_validate(event) for event in events]
