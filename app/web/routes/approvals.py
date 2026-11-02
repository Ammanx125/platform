# app/web/routes/approvals.py
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_permission
from app.core.config import settings
from app.db.models.action import ActionRecord
from app.db.session import get_db
from app.services.actions import approval as approval_service
from app.web.deps import HtmlUser
from app.web.routes.scenarios import require_scenario_csrf
from app.web.templating import base_context, templates
from app.web.view_helpers import build_action_timeline

router = APIRouter()


_ACTION_PRESENTATION = {
    "generate_report": (
        "Prepare a business report from the supporting evidence."
    ),
    "get_supplier_detail": "Review details for the selected supplier.",
    "list_suppliers": "Review the suppliers connected to this workspace.",
    "list_recent_anomalies": "Review recently detected business anomalies.",
}


def _action_presentation(record: ActionRecord, viewer_user_id: uuid.UUID) -> dict:
    validator_results = (record.validation_result or {}).get("validators", [])
    evidence_count = next(
        (
            detail["evidence_count"]
            for result in validator_results
            if isinstance(result, dict)
            and isinstance((detail := result.get("detail")), dict)
            and isinstance(detail.get("evidence_count"), int)
        ),
        None,
    )
    return {
        "a": record,
        "recommended_action": _ACTION_PRESENTATION.get(
            record.tool_name,
            f"Review: {record.tool_name.replace('_', ' ')}.",
        ),
        "evidence_count": evidence_count,
        # Distinct key: the row template passes it to the timeline partial
        # without clobbering any other `timeline` in the include scope.
        "action_timeline": build_action_timeline(record),
        # The proposer cannot approve their own action (maker-checker); the
        # row hides the buttons in that case instead of returning a 409.
        "can_decide": record.user_id != viewer_user_id,
    }


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
            "actions": [_action_presentation(row, user.id) for row in rows],
        },
    )


@router.post(
    "/approvals/{action_id}/approve",
    response_class=HTMLResponse,
    dependencies=[
        Depends(require_permission("action:approve")),
        Depends(require_scenario_csrf),
    ],
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
        {
            **_action_presentation(record, user.id),
            "request": request,
            "csrf_cookie_name": settings.csrf_cookie_name,
            "csrf_header_name": settings.csrf_header_name,
        },
    )


@router.post(
    "/approvals/{action_id}/reject",
    response_class=HTMLResponse,
    dependencies=[
        Depends(require_permission("action:reject")),
        Depends(require_scenario_csrf),
    ],
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
        {
            **_action_presentation(record, user.id),
            "request": request,
            "csrf_cookie_name": settings.csrf_cookie_name,
            "csrf_header_name": settings.csrf_header_name,
        },
    )