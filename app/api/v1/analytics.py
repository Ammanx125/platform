# app/api/v1/analytics.py
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentTenantId, require_permission
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.analytics import (
    KPIDefinitionRead,
    KPIEvaluateRequest,
    KPIResultRead,
)
from app.services.analytics import kpi as kpi_service

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/kpis", response_model=list[KPIDefinitionRead])
async def list_kpis(
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(require_permission("knowledge:read"))],
) -> list[KPIDefinitionRead]:
    defs = await kpi_service.list_kpis(db)
    return [KPIDefinitionRead.model_validate(d) for d in defs]


@router.get("/kpis/{key}", response_model=KPIDefinitionRead)
async def get_kpi(
    key: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(require_permission("knowledge:read"))],
) -> KPIDefinitionRead:
    definition = await kpi_service.load_kpi(db, key=key)
    if definition is None:
        raise HTTPException(status_code=404, detail="KPI not found")
    return KPIDefinitionRead.model_validate(definition)


@router.post(
    "/kpis/{key}/evaluate",
    response_model=KPIResultRead,
)
async def evaluate_kpi(
    key: str,
    body: KPIEvaluateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("decision:run"))],
) -> KPIResultRead:
    """
    Evaluate a KPI for the current tenant.

    Returns a result object even when the formula failed — the caller
    inspects `error` rather than catching an HTTP error.
    """
    try:
        result = await kpi_service.evaluate_kpi(
            db,
            key=key,
            tenant_id=tenant_id,
            source_ids=body.source_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return KPIResultRead(
        key=result.key,
        display_name=result.display_name,
        domain=result.domain,
        unit=result.unit,
        value_type=result.value_type,
        value=result.value,
        formula_trace=result.formula_trace,
        source_ids=result.source_ids,
        rows_considered=result.rows_considered,
        computed_at=result.computed_at,
        error=result.error,
    )


@router.get(
    "/kpis/{key}/value",
    response_model=KPIResultRead,
)
async def get_kpi_value(
    key: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("decision:run"))],
) -> KPIResultRead:
    """Convenience GET — same as POST /evaluate with no source filter."""
    try:
        result = await kpi_service.evaluate_kpi(
            db, key=key, tenant_id=tenant_id, source_ids=None
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return KPIResultRead(
        key=result.key,
        display_name=result.display_name,
        domain=result.domain,
        unit=result.unit,
        value_type=result.value_type,
        value=result.value,
        formula_trace=result.formula_trace,
        source_ids=result.source_ids,
        rows_considered=result.rows_considered,
        computed_at=result.computed_at,
        error=result.error,
    )