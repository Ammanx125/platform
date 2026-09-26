# app/api/v1/tool_policies.py
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentTenantId, require_permission
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.tool_policy import ToolPolicyRead, ToolPolicySet
from app.services.actions import policy as policy_service
from app.services.actions.registry import names as tool_names

router = APIRouter(prefix="/tool-policies", tags=["tool-policies"])


@router.get("", response_model=list[ToolPolicyRead])
async def list_policies(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("tool_policy:manage"))],
) -> list[ToolPolicyRead]:
    rows = await policy_service.list_overrides(db, tenant_id=tenant_id)
    return [ToolPolicyRead.model_validate(r) for r in rows]


@router.put("/{tool_name}", response_model=ToolPolicyRead)
async def set_policy(
    tool_name: str,
    body: ToolPolicySet,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    user: Annotated[User, Depends(require_permission("tool_policy:manage"))],
) -> ToolPolicyRead:
    if tool_name not in tool_names():
        raise HTTPException(
            status_code=404, detail=f"tool {tool_name!r} not registered"
        )
    try:
        row = await policy_service.set_override(
            db,
            tenant_id=tenant_id,
            tool_name=tool_name,
            policy_override=body.policy_override,
            actor_user_id=user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(row)
    return ToolPolicyRead.model_validate(row)


@router.delete("/{tool_name}", status_code=status.HTTP_204_NO_CONTENT)
async def clear_policy(
    tool_name: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("tool_policy:manage"))],
) -> None:
    removed = await policy_service.clear_override(
        db, tenant_id=tenant_id, tool_name=tool_name
    )
    if not removed:
        raise HTTPException(status_code=404, detail="no override set")
    await db.commit()