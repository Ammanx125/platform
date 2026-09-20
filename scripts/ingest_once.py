# scripts/ingest_once.py
"""
Trigger an ingestion for an existing source, from the CLI.

Usage:
    python -m scripts.ingest_once --source-id <uuid>
"""
from __future__ import annotations

import argparse
import asyncio
import uuid

from app.db.session import SessionLocal
from app.services.ingestion.service import run_job


async def main(source_id: uuid.UUID) -> None:
    async with SessionLocal() as db:
        job = await enqueue_and_run(db, source_id=source_id)
        print(f"job {job.id} status {job.status}")


async def enqueue_and_run(db, *, source_id: uuid.UUID):
    from sqlalchemy import select

    from app.db.models.dataset import DataSource, IngestionJob

    source = (
        await db.execute(select(DataSource).where(DataSource.id == source_id))
    ).scalar_one_or_none()
    if source is None:
        raise SystemExit(f"source not found: {source_id}")

    job = IngestionJob(
        tenant_id=source.tenant_id,
        source_id=source.id,
        status="pending",
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    await run_job(db, job_id=job.id)

    job = (
        await db.execute(select(IngestionJob).where(IngestionJob.id == job.id))
    ).scalar_one()
    return job


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--source-id", required=True, type=uuid.UUID)
    args = p.parse_args()
    asyncio.run(main(args.source_id))