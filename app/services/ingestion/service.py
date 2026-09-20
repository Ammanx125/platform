# app/services/ingestion/service.py
from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.dataset import (
    DataSource,
    IngestionJob,
    IngestionLineage,
    StagedRow,
)
from app.services.ingestion.base import IngestionError
from app.services.ingestion.registry import get_connector


def extension_of(filename: str) -> str:
    if "." not in filename:
        return ""
    return "." + filename.rsplit(".", 1)[-1].lower()


def extension_to_source_type(extension: str) -> str:
    mapping = {".csv": "csv", ".xlsx": "excel", ".xls": "excel"}
    try:
        return mapping[extension.lower()]
    except KeyError as exc:
        raise IngestionError(f"unsupported extension: {extension}") from exc


def build_storage_key(*, tenant_id: uuid.UUID, source_id: uuid.UUID, extension: str) -> str:
    return f"{tenant_id}/{source_id}/{uuid.uuid4()}{extension}"


def hash_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


async def enqueue_job(
    db: AsyncSession, *, tenant_id: uuid.UUID, source_id: uuid.UUID
) -> IngestionJob:
    job = IngestionJob(tenant_id=tenant_id, source_id=source_id, status="pending")
    db.add(job)
    await db.flush()
    return job


async def run_job(db: AsyncSession, *, job_id: uuid.UUID) -> None:
    """
    Executes one ingestion job to completion. Called by the worker.
    Marks status accordingly and never raises to the caller — errors are
    persisted on the job row.
    """
    job = (
        await db.execute(select(IngestionJob).where(IngestionJob.id == job_id))
    ).scalar_one_or_none()
    if job is None:
        return
    if job.status not in ("pending",):
        return

    source = (
        await db.execute(select(DataSource).where(DataSource.id == job.source_id))
    ).scalar_one_or_none()
    if source is None:
        job.status = "failed"
        job.error_message = "source not found"
        job.finished_at = datetime.now(UTC)
        await db.commit()
        return

    job.status = "running"
    job.started_at = datetime.now(UTC)
    await db.commit()

    try:
        lineage_row = (
            await db.execute(
                select(IngestionLineage).where(IngestionLineage.job_id == job.id)
            )
        ).scalar_one_or_none()
        if lineage_row is None:
            lineage_row = IngestionLineage(tenant_id=job.tenant_id, job_id=job.id)
            db.add(lineage_row)
            await db.flush()

        if source.source_type == "webhook":
            from app.services.ingestion import webhook as webhook_service
            result = await webhook_service.process_pending_deliveries(
                db, job=job
            )
        else:
            connector = get_connector(source.source_type)
            result = await connector.ingest(source=source)

        # Stage rows
        for parsed in result.rows:
            db.add(StagedRow(
                tenant_id=job.tenant_id,
                job_id=job.id,
                source_id=source.id,
                row_number=parsed.row_number,
                raw_data=parsed.data,
            ))

        job.rows_read = len(result.rows) + len(result.errors)
        job.rows_staged = len(result.rows)
        lineage_row.errors = result.errors
        lineage_row.encoding = result.metadata.encoding
        lineage_row.delimiter = result.metadata.delimiter

        # Flush staged rows so the profiler can see them, then compute the
        # profile. Kept inside the try so a profiling failure fails the job.
        await db.flush()
        from app.services.understanding.service import compute_profile
        await compute_profile(db, job_id=job.id)

        job.status = "succeeded"
        job.finished_at = datetime.now(UTC)
        await db.commit()

    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        # Re-fetch job to mark failure (rollback discarded the running state)
        job = (
            await db.execute(select(IngestionJob).where(IngestionJob.id == job_id))
        ).scalar_one_or_none()
        if job is not None:
            job.status = "failed"
            job.error_message = str(exc)[:2000]
            job.finished_at = datetime.now(UTC)
            await db.commit()


async def ingest_once(
    db: AsyncSession, *, tenant_id: uuid.UUID, source_id: uuid.UUID
) -> IngestionJob:
    """
    Enqueue and *synchronously run* an ingestion for a pull source.

    Used by POST /datasets/{id}/ingest for SQL and HTTP sources that don't
    go through the upload flow. The upload flow (CSV/Excel) already creates
    a job; this is the equivalent for pull connectors.

    Runs synchronously for now because SQL/HTTP ingests are typically fast.
    If a source is slow, the caller should still get a job_id back
    immediately — in that case, switch this to enqueue-only and let the
    worker pick it up.
    """
    job = await enqueue_job(db, tenant_id=tenant_id, source_id=source_id)
    await db.commit()
    await run_job(db, job_id=job.id)
    # re-fetch to return fresh state
    job = (
        await db.execute(select(IngestionJob).where(IngestionJob.id == job.id))
    ).scalar_one()
    return job