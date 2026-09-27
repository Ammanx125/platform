# app/web/routes/overview.py
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.action import ActionRecord
from app.db.models.analytics import Anomaly
from app.db.models.decision import DecisionRun
from app.db.models.tenant import Tenant
from app.db.session import get_db
from app.services.analytics import kpi as kpi_service
from app.web.deps import HtmlUser
from app.web.templating import base_context, templates

router = APIRouter()


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

    # KPIs: evaluate the catalog, keep those that returned a value.
    kpi_defs = await kpi_service.list_kpis(db)
    kpis = []
    for d in kpi_defs[:8]:
        result = await kpi_service.evaluate_kpi(
            db, key=d.key, tenant_id=tenant_id, source_ids=None
        )
        if result.error is None and result.value is not None:
            kpis.append(result)

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
            "pending_actions": pending,
            "recent_anomalies": anomalies,
            "recent_decisions": decisions,
        },
    )