# app/api/v1/events.py
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentTenantId, require_permission
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.event import EventRead
from app.services.events import store as events_store

router = APIRouter(prefix="/events", tags=["events"])


@router.get("", response_model=list[EventRead])
async def list_events(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("audit:read"))],
    event_type: str | None = None,
    source_id: uuid.UUID | None = None,
    since: datetime | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[EventRead]:
    rows = await events_store.list_events(
        db,
        tenant_id=tenant_id,
        event_type=event_type,
        source_id=source_id,
        since=since,
        status=status,
        limit=limit,
    )
    return [EventRead.model_validate(r) for r in rows]