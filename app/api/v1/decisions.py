# app/api/v1/decisions.py
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentTenantId, CurrentUser, require_permission
from app.core.config import settings
from app.db.models.decision import DecisionRun
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.decision import DecisionRequest, DecisionRunRead
from app.services.orchestration import executor
from app.services.orchestration.context import OrchestratorRequest

router = APIRouter(prefix="/decisions", tags=["decisions"])


async def _check_decision_quota(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    """Reject excessive orchestration requests before they consume expensive resources."""
    window_start = datetime.now(UTC) - timedelta(hours=1)

    tenant_count = await db.scalar(
        select(func.count(DecisionRun.id)).where(
            DecisionRun.tenant_id == tenant_id,
            DecisionRun.created_at >= window_start,
        )
    )
    if tenant_count is not None and tenant_count >= settings.decision_tenant_hourly_quota:
        raise HTTPException(
            status_code=429,
            detail=(
                "tenant decision quota exceeded; try again later "
                f"({settings.decision_tenant_hourly_quota}/hour)"
            ),
        )

    user_count = await db.scalar(
        select(func.count(DecisionRun.id)).where(
            DecisionRun.tenant_id == tenant_id,
            DecisionRun.user_id == user_id,
            DecisionRun.created_at >= window_start,
        )
    )
    if user_count is not None and user_count >= settings.decision_user_hourly_quota:
        raise HTTPException(
            status_code=429,
            detail=(
                "user decision quota exceeded; try again later "
                f"({settings.decision_user_hourly_quota}/hour)"
            ),
        )


@router.post("", response_model=DecisionRunRead, status_code=status.HTTP_201_CREATED)
async def run_decision(
    body: DecisionRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    user: CurrentUser,
    _perm: Annotated[User, Depends(require_permission("decision:run"))],
) -> DecisionRunRead:
    await _check_decision_quota(db, tenant_id=tenant_id, user_id=user.id)

    result = await executor.execute(
        db,
        request=OrchestratorRequest(
            query=body.query,
            domain_hint=body.domain_hint,
            source_ids=body.source_ids,
        ),
        tenant_id=tenant_id,
        user_id=user.id,
    )
    row = await executor.persist(db, result=result)
    await db.commit()
    await db.refresh(row)
    return DecisionRunRead.model_validate(row)


@router.get("", response_model=list[DecisionRunRead])
async def list_decisions(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("decision:run"))],
    limit: int = 50,
) -> list[DecisionRunRead]:
    stmt = (
        select(DecisionRun)
        .where(DecisionRun.tenant_id == tenant_id)
        .order_by(DecisionRun.created_at.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [DecisionRunRead.model_validate(r) for r in rows]


@router.get("/{decision_id}", response_model=DecisionRunRead)
async def get_decision(
    decision_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("decision:run"))],
) -> DecisionRunRead:
    row = (
        await db.execute(
            select(DecisionRun).where(
                DecisionRun.id == decision_id,
                DecisionRun.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="decision not found")
    return DecisionRunRead.model_validate(row)