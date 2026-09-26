# app/api/v1/analytics.py
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentTenantId, require_permission
from app.db.models.analytics import Forecast
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.analytics import (
    AnomalyDetectorRead,
    AnomalyRead,
    ForecastRead,
    ForecastRunRequest,
    KPIDefinitionRead,
    KPIEvaluateRequest,
    KPIResultRead,
    RunDetectorRequest,
)
from app.services.analytics import anomalies as anomalies_service
from app.services.analytics import forecasting as forecasting_service
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

# ---------- anomaly detectors ----------

@router.get("/anomaly-detectors", response_model=list[AnomalyDetectorRead])
async def list_anomaly_detectors(
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(require_permission("knowledge:read"))],
) -> list[AnomalyDetectorRead]:
    defs = await anomalies_service.list_detectors(db)
    return [AnomalyDetectorRead.model_validate(d) for d in defs]


@router.get(
    "/anomaly-detectors/{key}",
    response_model=AnomalyDetectorRead,
)
async def get_anomaly_detector(
    key: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(require_permission("knowledge:read"))],
) -> AnomalyDetectorRead:
    d = await anomalies_service.load_detector(db, key=key)
    if d is None:
        raise HTTPException(status_code=404, detail="detector not found")
    return AnomalyDetectorRead.model_validate(d)


@router.post(
    "/anomaly-detectors/{key}/run",
    response_model=list[AnomalyRead],
)
async def run_anomaly_detector(
    key: str,
    body: RunDetectorRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("decision:run"))],
) -> list[AnomalyRead]:
    try:
        anomalies = await anomalies_service.run_detector(
            db,
            detector_key=key,
            tenant_id=tenant_id,
            source_ids=body.source_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.commit()
    return [AnomalyRead.model_validate(a) for a in anomalies]


@router.post(
    "/anomaly-detectors/run-all",
    response_model=list[AnomalyRead],
)
async def run_all_anomaly_detectors(
    body: RunDetectorRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("decision:run"))],
) -> list[AnomalyRead]:
    anomalies = await anomalies_service.run_all_detectors(
        db, tenant_id=tenant_id, source_ids=body.source_ids
    )
    await db.commit()
    return [AnomalyRead.model_validate(a) for a in anomalies]


# ---------- anomalies ----------

@router.get("/anomalies", response_model=list[AnomalyRead])
async def list_anomalies(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("knowledge:read"))],
    detector_key: str | None = None,
    severity: str | None = None,
    group_key: str | None = None,
    since: datetime | None = None,
    limit: int = 100,
) -> list[AnomalyRead]:
    rows = await anomalies_service.list_anomalies(
        db,
        tenant_id=tenant_id,
        detector_key=detector_key,
        severity=severity,
        group_key=group_key,
        since=since,
        limit=limit,
    )
    return [AnomalyRead.model_validate(a) for a in rows]


@router.get("/anomalies/{anomaly_id}", response_model=AnomalyRead)
async def get_anomaly(
    anomaly_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("knowledge:read"))],
) -> AnomalyRead:
    a = await anomalies_service.get_anomaly(
        db, anomaly_id=anomaly_id, tenant_id=tenant_id
    )
    if a is None:
        raise HTTPException(status_code=404, detail="anomaly not found")
    return AnomalyRead.model_validate(a)

@router.post("/forecast/run", response_model=list[ForecastRead])
async def run_forecast(
    body: ForecastRunRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("decision:run"))],
) -> list[ForecastRead]:
    results = await forecasting_service.run_forecast_for_concept(
        db,
        tenant_id=tenant_id,
        value_concept=body.value_concept,
        group_by_concept=body.group_by_concept,
        horizon=body.horizon,
        source_ids=body.source_ids,
        requested_models=body.models,
    )
    await db.commit()
    # Re-read the persisted rows to return their ids.
    out: list[ForecastRead] = []
    for r in results:
        row = (
            await db.execute(
                select(Forecast)
                .where(
                    Forecast.tenant_id == tenant_id,
                    Forecast.value_concept == r.value_concept,
                    Forecast.group_key == r.group_key,
                    Forecast.trained_at == r.trained_at,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is not None:
            out.append(ForecastRead.model_validate(row))
    return out


@router.get("/forecasts", response_model=list[ForecastRead])
async def list_forecasts(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("knowledge:read"))],
    value_concept: str | None = None,
    group_key: str | None = None,
    since: datetime | None = None,
    limit: int = 100,
) -> list[ForecastRead]:
    rows = await forecasting_service.list_forecasts(
        db,
        tenant_id=tenant_id,
        value_concept=value_concept,
        group_key=group_key,
        since=since,
        limit=limit,
    )
    return [ForecastRead.model_validate(r) for r in rows]


@router.get("/forecasts/{forecast_id}", response_model=ForecastRead)
async def get_forecast(
    forecast_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("knowledge:read"))],
) -> ForecastRead:
    f = await forecasting_service.get_forecast(
        db, forecast_id=forecast_id, tenant_id=tenant_id
    )
    if f is None:
        raise HTTPException(status_code=404, detail="forecast not found")
    return ForecastRead.model_validate(f)