# app/api/v1/workflows.py
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentTenantId, require_permission
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.workflow import (
    WorkflowApproveRequest,
    WorkflowDefinitionRead,
    WorkflowInstanceRead,
    WorkflowResumeRequest,
    WorkflowStartRequest,
)
from app.services.workflows import engine as workflow_engine
from app.services.workflows.registry import all_workflows
from app.services.workflows.registry import get as get_workflow

router = APIRouter(prefix="/workflows", tags=["workflows"])


# ---------- definitions ----------

@router.get("/definitions", response_model=list[WorkflowDefinitionRead])
async def list_workflows(
    _user: Annotated[User, Depends(require_permission("knowledge:read"))],
) -> list[WorkflowDefinitionRead]:
    return [_definition_read(w) for w in all_workflows()]


@router.get("/definitions/{workflow_key}", response_model=WorkflowDefinitionRead)
async def get_workflow_definition(
    workflow_key: str,
    _user: Annotated[User, Depends(require_permission("knowledge:read"))],
) -> WorkflowDefinitionRead:
    wf = get_workflow(workflow_key)
    if wf is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    return _definition_read(wf)


def _definition_read(w) -> WorkflowDefinitionRead:
    return WorkflowDefinitionRead(
        key=w.key,
        display_name=w.display_name,
        description=w.description,
        domain=w.domain,
        step_count=len(w.steps),
        steps=[
            {"name": s.name, "type": s.type, "params": s.params}
            for s in w.steps
        ],
        requirements=[
            {"kind": r.kind, "key": r.key, "optional": r.optional}
            for r in w.requirements
        ],
        trigger_keywords=sorted(w.trigger_keywords),
    )


# ---------- instances ----------

@router.post(
    "/definitions/{workflow_key}/start",
    response_model=WorkflowInstanceRead,
    status_code=status.HTTP_201_CREATED,
)
async def start_workflow(
    workflow_key: str,
    body: WorkflowStartRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    user: Annotated[User, Depends(require_permission("workflow:manage"))],
) -> WorkflowInstanceRead:
    try:
        instance = await workflow_engine.start_workflow(
            db,
            workflow_key=workflow_key,
            tenant_id=tenant_id,
            user_id=user.id,
            source_ids=body.source_ids,
            trigger_type="manual",
            trigger_metadata=body.trigger_metadata,
            run_now=True,
        )
    except workflow_engine.WorkflowError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(instance)
    return WorkflowInstanceRead.model_validate(instance)


@router.get("/instances", response_model=list[WorkflowInstanceRead])
async def list_instances(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("knowledge:read"))],
    status_filter: str | None = None,
    workflow_key: str | None = None,
    limit: int = 100,
) -> list[WorkflowInstanceRead]:
    rows = await workflow_engine.list_instances(
        db,
        tenant_id=tenant_id,
        status=status_filter,
        workflow_key=workflow_key,
        limit=limit,
    )
    return [WorkflowInstanceRead.model_validate(r) for r in rows]


@router.get("/instances/{instance_id}", response_model=WorkflowInstanceRead)
async def get_instance(
    instance_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("knowledge:read"))],
) -> WorkflowInstanceRead:
    row = await workflow_engine.get_instance(
        db, instance_id=instance_id, tenant_id=tenant_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="instance not found")
    return WorkflowInstanceRead.model_validate(row)


@router.post("/instances/{instance_id}/approve", response_model=WorkflowInstanceRead)
async def approve_instance_endpoint(
    instance_id: uuid.UUID,
    body: WorkflowApproveRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    user: Annotated[User, Depends(require_permission("action:approve"))],
) -> WorkflowInstanceRead:
    try:
        instance = await workflow_engine.approve_instance(
            db,
            instance_id=instance_id,
            tenant_id=tenant_id,
            approver_user_id=user.id,
            note=body.note,
        )
    except workflow_engine.WorkflowError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(instance)
    return WorkflowInstanceRead.model_validate(instance)

@router.post("/instances/{instance_id}/resume", response_model=WorkflowInstanceRead)
async def resume_instance_endpoint(
    instance_id: uuid.UUID,
    body: WorkflowResumeRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    user: Annotated[User, Depends(require_permission("workflow:manage"))],
) -> WorkflowInstanceRead:
    try:
        instance = await workflow_engine.get_instance(
            db, instance_id=instance_id, tenant_id=tenant_id
        )
        if instance is None:
            raise HTTPException(status_code=404, detail="instance not found")
        instance = await workflow_engine.resume_instance(
            db, instance=instance, user_id=user.id, source_ids=body.source_ids
        )
    except workflow_engine.WorkflowError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(instance)
    return WorkflowInstanceRead.model_validate(instance)