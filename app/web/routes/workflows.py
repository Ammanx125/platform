# app/web/routes/workflows.py
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.workflow import WorkflowInstance
from app.db.session import get_db
from app.services.workflows.registry import all_workflows
from app.services.workflows.registry import get as get_workflow
from app.web.deps import HtmlUser
from app.web.templating import base_context, templates

router = APIRouter()


@router.get("/workflows", response_class=HTMLResponse)
async def workflows_list(
    request: Request,
    user: HtmlUser,
):
    return templates.TemplateResponse(
        request,
        "workflows/list.html",
        {
            **base_context(),
            "loop_stage": "action",
            "active_nav": "workflows",
            "tenant_name": str(user.tenant_id),
            "user_email": user.email,
            "workflows": all_workflows(),
        },
    )


@router.get("/workflows/instances", response_class=HTMLResponse)
async def instances_list(
    request: Request,
    user: HtmlUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    workflow_key: str | None = None,
    status: str | None = None,
    limit: int = 100,
):
    stmt = (
        select(WorkflowInstance)
        .where(WorkflowInstance.tenant_id == user.tenant_id)
        .order_by(WorkflowInstance.created_at.desc())
        .limit(limit)
    )
    if workflow_key is not None:
        stmt = stmt.where(WorkflowInstance.workflow_key == workflow_key)
    if status is not None:
        stmt = stmt.where(WorkflowInstance.status == status)

    rows = (await db.execute(stmt)).scalars().all()
    return templates.TemplateResponse(
        request,
        "workflows/instances.html",
        {
            **base_context(),
            "loop_stage": "action",
            "active_nav": "workflows",
            "tenant_name": str(user.tenant_id),
            "user_email": user.email,
            "instances": rows,
            "current_workflow_key": workflow_key,
            "current_status": status,
        },
    )


@router.get("/workflows/instances/{instance_id}", response_class=HTMLResponse)
async def instance_detail(
    instance_id: uuid.UUID,
    request: Request,
    user: HtmlUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    instance = (
        await db.execute(
            select(WorkflowInstance).where(
                WorkflowInstance.id == instance_id,
                WorkflowInstance.tenant_id == user.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if instance is None:
        raise HTTPException(status_code=404, detail="instance not found")

    definition = get_workflow(instance.workflow_key)

    return templates.TemplateResponse(
        request,
        "workflows/instance_detail.html",
        {
            **base_context(),
            "loop_stage": "action",
            "active_nav": "workflows",
            "tenant_name": str(user.tenant_id),
            "user_email": user.email,
            "instance": instance,
            "definition": definition,
        },
    )