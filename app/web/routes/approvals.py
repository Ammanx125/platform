# app/web/routes/approvals.py
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_csrf
from app.db.models.action import ActionRecord
from app.db.session import get_db
from app.services.actions import approval as approval_service
from app.web.deps import HtmlUser
from app.web.templating import base_context, templates

router = APIRouter()


@router.get("/approvals", response_class=HTMLResponse)
async def approvals_list(
    request: Request,
    user: HtmlUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    rows = (
        await db.execute(
            select(ActionRecord)
            .where(
                ActionRecord.tenant_id == user.tenant_id,
                ActionRecord.status == "pending_approval",
            )
            .order_by(ActionRecord.proposed_at.desc())
            .limit(100)
        )
    ).scalars().all()
    return templates.TemplateResponse(
        request,
        "approvals/list.html",
        {
            **base_context(),
            "loop_stage": "action",
            "active_nav": "approvals",
            "tenant_name": str(user.tenant_id),
            "user_email": user.email,
            "actions": rows,
        },
    )


@router.post(
    "/approvals/{action_id}/approve",
    response_class=HTMLResponse,
    dependencies=[Depends(require_csrf)],
)
async def approve(
    action_id: uuid.UUID,
    request: Request,
    user: HtmlUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    try:
        record = await approval_service.approve(
            db,
            action_id=action_id,
            tenant_id=user.tenant_id,
            approver_user_id=user.id,
        )
    except approval_service.ApprovalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(record)
    return templates.TemplateResponse(
        request,
        "approvals/_row.html",
        {"a": record},
    )


@router.post(
    "/approvals/{action_id}/reject",
    response_class=HTMLResponse,
    dependencies=[Depends(require_csrf)],
)
async def reject(
    action_id: uuid.UUID,
    request: Request,
    user: HtmlUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    try:
        record = await approval_service.reject(
            db,
            action_id=action_id,
            tenant_id=user.tenant_id,
            rejector_user_id=user.id,
            reason=None,
        )
    except approval_service.ApprovalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(record)
    return templates.TemplateResponse(
        request,
        "approvals/_row.html",
        {"a": record},
    )