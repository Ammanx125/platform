# app/web/routes/datasets.py
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.dataset import DataSource, IngestionJob
from app.db.models.semantic import SemanticMapping
from app.db.models.understanding import DataProfile
from app.db.session import get_db
from app.web.deps import HtmlUser
from app.web.templating import base_context, templates

router = APIRouter()


@router.get("/datasets", response_class=HTMLResponse)
async def datasets_list(
    request: Request,
    user: HtmlUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    sources = (
        await db.execute(
            select(DataSource)
            .where(DataSource.tenant_id == user.tenant_id)
            .order_by(DataSource.created_at.desc())
        )
    ).scalars().all()

    # Job counts per source — simple aggregate.
    job_counts: dict[uuid.UUID, dict[str, int | str | datetime | None]] = {}
    for s in sources:
        jobs = (
            await db.execute(
                select(IngestionJob)
                .where(IngestionJob.source_id == s.id)
                .order_by(IngestionJob.created_at.desc())
                .limit(1)
            )
        ).scalars().all()
        latest = jobs[0] if jobs else None
        total = (
            await db.execute(
                select(IngestionJob).where(IngestionJob.source_id == s.id)
            )
        ).scalars().all()
        job_counts[s.id] = {
            "total": len(total),
            "succeeded": sum(1 for j in total if j.status == "succeeded"),
            "failed": sum(1 for j in total if j.status == "failed"),
            "latest_status": latest.status if latest else "—",
            "latest_finished": latest.finished_at if latest else None,
        }

    return templates.TemplateResponse(
        request,
        "datasets/list.html",
        {
            **base_context(),
            "loop_stage": "data",
            "active_nav": "datasets",
            "tenant_name": str(user.tenant_id),
            "user_email": user.email,
            "sources": sources,
            "job_counts": job_counts,
        },
    )


@router.get("/datasets/{dataset_id}", response_class=HTMLResponse)
async def dataset_detail(
    dataset_id: uuid.UUID,
    request: Request,
    user: HtmlUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    source = (
        await db.execute(
            select(DataSource).where(
                DataSource.id == dataset_id,
                DataSource.tenant_id == user.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=404, detail="dataset not found")

    jobs = (
        await db.execute(
            select(IngestionJob)
            .where(IngestionJob.source_id == dataset_id)
            .order_by(IngestionJob.created_at.desc())
            .limit(20)
        )
    ).scalars().all()

    mappings = (
        await db.execute(
            select(SemanticMapping)
            .where(SemanticMapping.source_id == dataset_id)
            .order_by(SemanticMapping.source_column)
        )
    ).scalars().all()

    latest_profile = None
    if jobs:
        latest_profile = (
            await db.execute(
                select(DataProfile).where(DataProfile.job_id == jobs[0].id)
            )
        ).scalar_one_or_none()

    return templates.TemplateResponse(
        request,
        "datasets/detail.html",
        {
            **base_context(),
            "loop_stage": "data",
            "active_nav": "datasets",
            "tenant_name": str(user.tenant_id),
            "user_email": user.email,
            "source": source,
            "jobs": jobs,
            "mappings": mappings,
            "profile": latest_profile,
        },
    )