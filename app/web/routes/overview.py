# app/web/routes/overview.py
from __future__ import annotations

import math
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.action import ActionRecord
from app.db.models.analytics import Anomaly, Forecast
from app.db.models.dataset import DataSource
from app.db.models.decision import DecisionRun
from app.db.models.semantic import CanonicalConcept, SemanticMapping
from app.db.models.tenant import Tenant
from app.db.session import get_db
from app.services.analytics import forecasting as forecasting_service
from app.services.analytics import kpi as kpi_service
from app.web.deps import HtmlUser
from app.web.templating import base_context, templates

router = APIRouter()


def _forecast_plot_points(forecast: Forecast) -> list[str]:
    values = [
        float(point["value"])
        for point in forecast.predicted_points
        if math.isfinite(float(point["value"]))
    ]
    if not values:
        return []

    width = 720
    top, bottom = 12.0, 156.0
    minimum, maximum = min(values), max(values)
    spread = maximum - minimum or 1.0
    denominator = max(len(values) - 1, 1)
    return [
        f"{index * width / denominator:.1f},"
        f"{bottom - (value - minimum) * (bottom - top) / spread:.1f}"
        for index, value in enumerate(values)
    ]


@router.get("/", response_class=HTMLResponse)
async def overview(
    request: Request,
    user: HtmlUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    tenant_id = user.tenant_id
    tenant = (
        await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    tenant_name = tenant.name if tenant else str(tenant_id)

    # Recent decisions
    decisions = (
        await db.execute(
            select(DecisionRun)
            .where(DecisionRun.tenant_id == tenant_id)
            .order_by(DecisionRun.created_at.desc())
            .limit(5)
        )
    ).scalars().all()

    # Pending approvals
    pending = (
        await db.execute(
            select(ActionRecord)
            .where(
                ActionRecord.tenant_id == tenant_id,
                ActionRecord.status == "pending_approval",
            )
            .order_by(ActionRecord.proposed_at.desc())
            .limit(5)
        )
    ).scalars().all()

    # Recent anomalies
    anomalies = (
        await db.execute(
            select(Anomaly)
            .where(Anomaly.tenant_id == tenant_id)
            .order_by(Anomaly.detected_at.desc())
            .limit(5)
        )
    ).scalars().all()
    pending_count = (
        await db.execute(
            select(func.count())
            .select_from(ActionRecord)
            .where(
                ActionRecord.tenant_id == tenant_id,
                ActionRecord.status == "pending_approval",
            )
        )
    ).scalar_one()

    # KPIs: evaluate the catalog, keep those that returned a value.
    kpi_defs = await kpi_service.list_kpis(db)
    kpis = []
    for d in kpi_defs[:8]:
        result = await kpi_service.evaluate_kpi(
            db, key=d.key, tenant_id=tenant_id, source_ids=None
        )
        if result.error is None and result.value is not None:
            kpis.append(result)

    saved_forecasts = await forecasting_service.list_forecasts(
        db, tenant_id=tenant_id, limit=20
    )
    latest_forecast = next(
        (
            forecast for forecast in saved_forecasts
            if forecast.status == "ok" and forecast.predicted_points
        ),
        None,
    )
    forecast_plot_points = (
        _forecast_plot_points(latest_forecast) if latest_forecast else []
    )

    mapped_source_count = (
        await db.execute(
            select(func.count(func.distinct(SemanticMapping.source_id)))
            .join(DataSource, DataSource.id == SemanticMapping.source_id)
            .where(
                SemanticMapping.tenant_id == tenant_id,
                SemanticMapping.status == "confirmed",
                DataSource.is_active.is_(True),
            )
        )
    ).scalar_one()
    confirmed_mapping_count = (
        await db.execute(
            select(func.count())
            .select_from(SemanticMapping)
            .join(DataSource, DataSource.id == SemanticMapping.source_id)
            .where(
                SemanticMapping.tenant_id == tenant_id,
                SemanticMapping.status == "confirmed",
                DataSource.is_active.is_(True),
            )
        )
    ).scalar_one()
    semantic_examples = (
        await db.execute(
            select(
                DataSource.id,
                DataSource.name,
                SemanticMapping.source_column,
                CanonicalConcept.display_name,
                CanonicalConcept.domain,
            )
            .join(SemanticMapping, SemanticMapping.source_id == DataSource.id)
            .join(
                CanonicalConcept,
                CanonicalConcept.key == SemanticMapping.canonical_concept_key,
            )
            .where(
                DataSource.tenant_id == tenant_id,
                DataSource.is_active.is_(True),
                SemanticMapping.tenant_id == tenant_id,
                SemanticMapping.status == "confirmed",
            )
            .order_by(SemanticMapping.source_column, DataSource.name)
            .limit(6)
        )
    ).all()

    return templates.TemplateResponse(
        request,
        "overview.html",
        {
            **base_context(),
            "loop_stage": "decision",
            "active_nav": "overview",
            "tenant_name": tenant_name,
            "user_email": user.email,
            "kpis": kpis,
            "pending_count": pending_count,
            "pending_actions": pending,
            "recent_anomalies": anomalies,
            "recent_decisions": decisions,
            "latest_forecast": latest_forecast,
            "forecast_plot_points": forecast_plot_points,
            "mapped_source_count": mapped_source_count,
            "confirmed_mapping_count": confirmed_mapping_count,
            "semantic_examples": semantic_examples,
        },
    )