# app/api/v1/actions.py
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentTenantId, require_permission
from app.db.models.action import ActionRecord
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.action import ActionRecordRead

router = APIRouter(prefix="/actions", tags=["actions"])


@router.get("", response_model=list[ActionRecordRead])
async def list_actions(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("action:propose"))],
    status_filter: str | None = None,
    tool_name: str | None = None,
    limit: int = 100,
) -> list[ActionRecordRead]:
    stmt = (
        select(ActionRecord)
        .where(ActionRecord.tenant_id == tenant_id)
        .order_by(ActionRecord.proposed_at.desc())
        .limit(limit)
    )
    if status_filter is not None:
        stmt = stmt.where(ActionRecord.status == status_filter)
    if tool_name is not None:
        stmt = stmt.where(ActionRecord.tool_name == tool_name)
    rows = (await db.execute(stmt)).scalars().all()
    return [ActionRecordRead.model_validate(r) for r in rows]


@router.get("/{action_id}", response_model=ActionRecordRead)
async def get_action(
    action_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("action:propose"))],
) -> ActionRecordRead:
    row = (
        await db.execute(
            select(ActionRecord).where(
                ActionRecord.id == action_id,
                ActionRecord.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="action not found")
    return ActionRecordRead.model_validate(row)