# app/web/routes/email.py
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.email import service as email_service
from app.services.email.service import EmailServiceError
from app.web.deps import HtmlUser
from app.web.templating import base_context, templates

router = APIRouter()


@router.get("/email", response_class=HTMLResponse)
async def email_overview(
    request: Request,
    user: HtmlUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    accounts = await email_service.list_accounts(db, tenant_id=user.tenant_id)
    return templates.TemplateResponse(
        request,
        "email/list.html",
        {
            **base_context(),
            "loop_stage": "understanding",
            "active_nav": "email",
            "tenant_name": "",
            "user_email": user.email,
            "user_name": user.full_name or user.email,
            "accounts": accounts,
        },
    )


@router.get("/email/accounts/{account_id}", response_class=HTMLResponse)
async def email_account_detail(
    account_id: uuid.UUID,
    request: Request,
    user: HtmlUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    intent: str | None = Query(default=None),
    urgency: str | None = Query(default=None),
):
    account = await email_service.get_account(
        db, tenant_id=user.tenant_id, account_id=account_id
    )
    if account is None:
        return RedirectResponse(url="/email", status_code=303)

    messages = await email_service.list_messages(
        db,
        tenant_id=user.tenant_id,
        account_id=account_id,
        limit=50,
        intent=intent,
        urgency=urgency,
    )
    return templates.TemplateResponse(
        request,
        "email/detail.html",
        {
            **base_context(),
            "loop_stage": "understanding",
            "active_nav": "email",
            "user_email": user.email,
            "user_name": user.full_name or user.email,
            "account": account,
            "messages": messages,
            "intent_filter": intent,
            "urgency_filter": urgency,
        },
    )


@router.get("/email/connect/error", response_class=HTMLResponse)
async def email_connect_error(
    request: Request,
    user: HtmlUser,
    reason: str = Query(default="unknown"),
):
    return templates.TemplateResponse(
        request,
        "email/error.html",
        {
            **base_context(),
            "active_nav": "email",
            "user_email": user.email,
            "user_name": user.full_name or user.email,
            "reason": reason,
        },
    )