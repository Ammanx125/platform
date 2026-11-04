# app/web/routes/decisions.py
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.decision import DecisionRun
from app.db.session import get_db
from app.services.orchestration.report_builder import report_from_stored
from app.services.orchestration.report_store import (
    find_structured_report_document_id,
    load_structured_report,
)
from app.web.deps import HtmlUser, require_html_permission
from app.web.templating import base_context, templates

router = APIRouter()


@router.get(
    "/decisions",
    response_class=HTMLResponse,
    dependencies=[Depends(require_html_permission("decision:run"))],
)
async def decisions_list(
    request: Request,
    user: HtmlUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    rows = (
        await db.execute(
            select(DecisionRun)
            .where(DecisionRun.tenant_id == user.tenant_id)
            .order_by(DecisionRun.created_at.desc())
            .limit(50)
        )
    ).scalars().all()
    return templates.TemplateResponse(
        request,
        "decisions/list.html",
        {
            **base_context(),
            "loop_stage": "decision",
            "active_nav": "decisions",
            "tenant_name": str(user.tenant_id),
            "user_email": user.email,
            "decisions": rows,
        },
    )


@router.get(
    "/decisions/{decision_id}",
    response_class=HTMLResponse,
    dependencies=[Depends(require_html_permission("decision:run"))],
)
async def decision_detail(
    decision_id: uuid.UUID,
    request: Request,
    user: HtmlUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    row = (
        await db.execute(
            select(DecisionRun).where(
                DecisionRun.id == decision_id,
                DecisionRun.tenant_id == user.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="decision not found")

    structured_report = await load_structured_report(
        db,
        tenant_id=user.tenant_id,
        decision_run_id=row.id,
    )
    report = report_from_stored(
        row.query,
        row.evidence,
        structured_report=structured_report,
    )

    report_document_id = (
        await find_structured_report_document_id(
            db, tenant_id=user.tenant_id, decision_run_id=row.id
        )
        if structured_report is not None
        else None
    )

    return templates.TemplateResponse(
        request,
        "decisions/detail.html",
        {
            **base_context(),
            "loop_stage": "decision",
            "active_nav": "decisions",
            "tenant_name": str(user.tenant_id),
            "user_email": user.email,
            "decision": row,
            "report": report,
            "report_document_id": report_document_id,
        },
    )