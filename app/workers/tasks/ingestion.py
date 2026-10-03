# app/workers/tasks/ingestion.py
"""
Ingestion task: process one pending IngestionJob.
"""
from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.dataset import DataSource, IngestionJob
from app.services.ingestion.service import run_job


async def run_ingestion_once(db: AsyncSession) -> int:
    stmt = (
        select(IngestionJob)
        .join(DataSource, DataSource.id == IngestionJob.source_id)
        .where(IngestionJob.status == "pending")
        .where(
            or_(
                DataSource.source_type != "agent",
                IngestionJob.storage_key.is_not(None),
            )
        )
        .order_by(IngestionJob.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True, of=IngestionJob)
    )
    job = (await db.execute(stmt)).scalar_one_or_none()
    if job is None:
        return 0
    job_id = job.id
    await db.commit()
    await run_job(db, job_id=job_id)
    return 1