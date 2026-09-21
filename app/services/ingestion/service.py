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
    mapping = {
        ".csv": "csv",
        ".xlsx": "excel",
        ".xls": "excel",
        ".pdf": "pdf",
    }
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


async def _run_pdf_job(
    db: AsyncSession,
    *,
    job: IngestionJob,
    source: DataSource,
    lineage_row: IngestionLineage,
) -> None:
    """
    PDF ingestion pipeline. Produces a Document, not StagedRows.

    Reads the file from storage, extracts blocks via PDFLoader, and hands
    them to knowledge_service.create_document. Writes extraction metadata
    and any per-page errors to lineage.
    """
    from app.services.ingestion.pdf import PDFLoader
    from app.services.knowledge import service as knowledge_service
    from app.services.storage.local import storage

    storage_key = source.config.get("storage_key")
    if not storage_key:
        raise IngestionError("pdf source has no storage_key in config")

    content = await storage.get(key=storage_key)
    if not content:
        raise IngestionError("pdf file is empty")

    loader = PDFLoader()
    extraction = await loader.extract(content=content)

    # Title: prefer PDF metadata, fall back to source name.
    title = extraction.title or source.name

    doc_metadata = {
        **extraction.doc_metadata,
        "original_filename": source.config.get("original_filename"),
        "file_size_bytes": len(content),
    }

    doc = await knowledge_service.create_document(
        db,
        tenant_id=job.tenant_id,
        title=title,
        content_type="pdf",
        raw_text="\n\n".join(b.text for b in extraction.blocks if b.text),
        blocks=extraction.blocks,
        source_id=source.id,
        doc_metadata=doc_metadata,
    )

    # Lineage: page count, chars, extraction errors. The rows_read /
    # rows_staged fields on the job don't map cleanly to documents, so we
    # record the page count there and the rest in lineage.
    job.rows_read = extraction.page_count
    job.rows_staged = doc.chunk_count
    lineage_row.errors = extraction.errors
    lineage_row.source_uri = storage_key
    lineage_row.byte_size = len(content)
    lineage_row.file_hash = hash_bytes(content)
    lineage_row.original_filename = source.config.get("original_filename")

    await db.flush()

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
        elif source.source_type == "pdf":
            await _run_pdf_job(db, job=job, source=source, lineage_row=lineage_row)
            job.status = "succeeded"
            job.finished_at = datetime.now(UTC)
            await db.commit()
            return
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


async def enqueue_ingest(
    db: AsyncSession, *, tenant_id: uuid.UUID, source_id: uuid.UUID
) -> IngestionJob:
    """
    Enqueue an ingestion job for a pull source. Returns the pending job.

    Does NOT run the job. The worker picks it up. Callers poll
    GET /datasets/{id}/jobs/{job_id} for status.
    """
    source = (
        await db.execute(
            select(DataSource).where(
                DataSource.id == source_id,
                DataSource.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if source is None:
        raise ValueError(f"source not found: {source_id}")
    if not source.is_active:
        raise ValueError("source is inactive")

    job = await enqueue_job(db, tenant_id=tenant_id, source_id=source_id)
    await db.flush()
    return job