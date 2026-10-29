# app/api/v1/scenarios.py
"""
Scenario Lab API.

Contract notes:
  - The tenant always comes from the authenticated user, never from the
    request body. ``source_id`` is verified against the tenant inside the
    baseline resolver.
  - Preview (`POST /preview`) is the cheap, read-only operation; save
    (`POST ""`) persists. Both run the same engine on the same baseline,
    so a preview and a saved scenario with equal inputs agree.
  - Missing prerequisites return 422 with a structured body naming the
    missing concept(s): the UI should explain, not just fail.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentTenantId, require_permission
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.scenarios import (
    ScenarioCalculateRequest,
    ScenarioCapabilitiesRead,
    ScenarioPreviewResponse,
    ScenarioRead,
    ScenarioSaveRequest,
    ScenarioSummaryRead,
    SourceCapabilityRead,
)
from app.services.scenarios import engine
from app.services.scenarios import service as scenario_service
from app.services.scenarios.baseline import BaselineUnavailable

router = APIRouter(prefix="/scenarios", tags=["scenarios"])


def _baseline_error(exc: BaselineUnavailable) -> HTTPException:
    status_code = (
        status.HTTP_404_NOT_FOUND
        if exc.code == "source_not_found"
        else status.HTTP_422_UNPROCESSABLE_ENTITY
    )
    return HTTPException(
        status_code=status_code,
        detail={
            "code": exc.code,
            "message": exc.detail,
            "missing_concepts": exc.missing,
        },
    )


@router.get("/capabilities", response_model=ScenarioCapabilitiesRead)
async def scenario_capabilities(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("dataset:read"))],
) -> ScenarioCapabilitiesRead:
    """
    Which sources can run the supplier-spend scenario, and why the others
    cannot. Computed from confirmed mappings and staged rows only.
    """
    sources: list[SourceCapabilityRead] = await scenario_service.capabilities(
        db, tenant_id=tenant_id
    )
    return ScenarioCapabilitiesRead(
        scenario_type=engine.SCENARIO_TYPE,
        formula_version=engine.FORMULA_VERSION,
        price_change_bounds={
            "min_pct": engine.SUPPLIER_PRICE_CHANGE_MIN,
            "max_pct": engine.SUPPLIER_PRICE_CHANGE_MAX,
        },
        supported=any(s.supported for s in sources),
        sources=sources,
        limitations=engine.default_limitations(),
    )


@router.post("/preview", response_model=ScenarioPreviewResponse)
async def preview_scenario(
    body: ScenarioCalculateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("dataset:read"))],
) -> ScenarioPreviewResponse:
    """Calculate without saving. Read-only and side-effect free."""
    try:
        result_document, baseline, _result = await scenario_service.calculate(
            db,
            tenant_id=tenant_id,
            source_id=body.source_id,
            price_change_pct=body.assumptions.supplier_price_change_pct,
        )
    except BaselineUnavailable as exc:
        raise _baseline_error(exc) from exc
    except scenario_service.ScenarioServiceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    provenance = dict(baseline.to_snapshot())
    provenance["source_id"] = baseline.source_id
    provenance["calculated_at"] = datetime.now(UTC).isoformat()
    return ScenarioPreviewResponse(
        result=scenario_service.result_read(result_document),
        provenance=provenance,
        calculated_at=datetime.now(UTC),
    )


@router.post(
    "",
    response_model=ScenarioRead,
    status_code=status.HTTP_201_CREATED,
)
async def save_scenario(
    body: ScenarioSaveRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    user: Annotated[User, Depends(require_permission("dataset:write"))],
) -> ScenarioRead:
    """Calculate and persist a named scenario with its frozen baseline."""
    try:
        scenario = await scenario_service.save(
            db,
            tenant_id=tenant_id,
            user_id=user.id,
            name=body.name,
            source_id=body.source_id,
            price_change_pct=body.assumptions.supplier_price_change_pct,
        )
    except BaselineUnavailable as exc:
        raise _baseline_error(exc) from exc
    except scenario_service.ScenarioServiceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(scenario)
    return scenario_service.to_read(scenario)


@router.get("", response_model=list[ScenarioSummaryRead])
async def list_scenarios(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("dataset:read"))],
    limit: int = 100,
) -> list[ScenarioSummaryRead]:
    if not 1 <= limit <= 200:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 200")
    rows = await scenario_service.list_scenarios(
        db, tenant_id=tenant_id, limit=limit
    )
    return [scenario_service.to_summary(r) for r in rows]


@router.get("/{scenario_id}", response_model=ScenarioRead)
async def get_scenario(
    scenario_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("dataset:read"))],
) -> ScenarioRead:
    try:
        scenario = await scenario_service.get(
            db, tenant_id=tenant_id, scenario_id=scenario_id
        )
    except scenario_service.ScenarioNotFound as exc:
        # 404 for another tenant's scenario too: never confirm it exists.
        raise HTTPException(status_code=404, detail="scenario not found") from exc
    return scenario_service.to_read(scenario)


@router.post("/{scenario_id}/recalculate", response_model=ScenarioRead)
async def recalculate_scenario(
    scenario_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    user: Annotated[User, Depends(require_permission("dataset:write"))],
) -> ScenarioRead:
    """
    Re-run against the current baseline using the saved assumptions.
    If the source can no longer support the scenario, the run is marked
    stale and the previous numbers are left intact.
    """
    try:
        scenario = await scenario_service.recalculate(
            db, tenant_id=tenant_id, scenario_id=scenario_id, user_id=user.id
        )
    except scenario_service.ScenarioNotFound as exc:
        raise HTTPException(status_code=404, detail="scenario not found") from exc
    except BaselineUnavailable as exc:
        await db.commit()
        raise _baseline_error(exc) from exc
    except scenario_service.ScenarioServiceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(scenario)
    return scenario_service.to_read(scenario)


@router.post("/stale-scan", response_model=dict)
async def scan_stale_scenarios(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("dataset:write"))],
) -> dict[str, int]:
    """
    Flag saved scenarios whose source data has moved on since they were
    calculated. Safe to call repeatedly.
    """
    updated = await scenario_service.mark_stale_scenarios(db, tenant_id=tenant_id)
    await db.commit()
    return {"marked_stale": updated}


__all__ = ["router"]
