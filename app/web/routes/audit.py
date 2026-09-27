# app/web/routes/audit.py
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.audit import service as audit_service
from app.services.audit.types import ALL_AUDIT_EVENT_TYPES
from app.web.deps import HtmlUser
from app.web.templating import base_context, templates

router = APIRouter()


@router.get("/audit", response_class=HTMLResponse)
async def audit_list(
    request: Request,
    user: HtmlUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    event_type: str | None = None,
    before: datetime | None = None,
    limit: int = 100,
):
    events = await audit_service.list_events(
        db,
        tenant_id=user.tenant_id,
        event_type=event_type,
        before=before,
        limit=limit,
    )
    return templates.TemplateResponse(
        request,
        "audit/list.html",
        {
            **base_context(),
            "loop_stage": "decision",
            "active_nav": "audit",
            "tenant_name": str(user.tenant_id),
            "user_email": user.email,
            "events": events,
            "current_filter": event_type,
            "known_types": sorted(ALL_AUDIT_EVENT_TYPES),
        },
    )