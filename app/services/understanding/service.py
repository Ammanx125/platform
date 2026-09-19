# app/services/understanding/service.py
"""
Orchestration for the understanding step: load staged rows, profile,
assess quality, persist.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.dataset import IngestionJob, StagedRow
from app.db.models.understanding import DataProfile
from app.services.understanding.profiler import profile_rows
from app.services.understanding.quality import assess_quality


async def compute_profile(
    db: AsyncSession, *, job_id: uuid.UUID
) -> DataProfile:
    """
    Compute (or recompute) the profile for one ingestion job.

    Idempotent: if a DataProfile already exists for the job, it is replaced.
    Raises ValueError if the job does not exist.
    """
    job = (
        await db.execute(select(IngestionJob).where(IngestionJob.id == job_id))
    ).scalar_one_or_none()
    if job is None:
        raise ValueError(f"job not found: {job_id}")

    rows = (
        await db.execute(
            select(StagedRow)
            .where(StagedRow.job_id == job_id)
            .order_by(StagedRow.row_number.asc())
        )
    ).scalars().all()

    raw = [r.raw_data for r in rows]

    result = profile_rows(raw)
    issues = assess_quality(result, raw)

    existing = (
        await db.execute(
            select(DataProfile).where(DataProfile.job_id == job_id)
        )
    ).scalar_one_or_none()

    if existing is None:
        existing = DataProfile(
            tenant_id=job.tenant_id,
            job_id=job.id,
            source_id=job.source_id,
            profiled_at=datetime.now(timezone.utc),
        )
        db.add(existing)

    existing.profiled_at = datetime.now(timezone.utc)
    existing.row_count = result.row_count
    existing.column_count = result.column_count
    existing.profile = result.to_dict()
    existing.issues = [i.to_dict() for i in issues]

    await db.flush()
    return existing