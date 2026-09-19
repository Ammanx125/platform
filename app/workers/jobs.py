# app/workers/jobs.py
"""
Ingestion worker.

Run alongside the API:
    python -m app.workers.jobs

Polls ingestion_jobs for pending rows and processes them one at a time.
Concurrency at the DB level is handled by FOR UPDATE SKIP LOCKED, so you can
run multiple worker processes safely.
"""
from __future__ import annotations

import asyncio
import logging
import signal

from sqlalchemy import select, text

from app.core.config import settings
from app.db.models.dataset import IngestionJob
from app.db.session import SessionLocal
from app.services.ingestion.service import run_job

logger = logging.getLogger("sansa.worker")


async def claim_one_job(db) -> IngestionJob | None:
    """
    Atomically claim one pending job. Uses FOR UPDATE SKIP LOCKED so
    multiple workers don't step on each other.
    """
    stmt = (
        select(IngestionJob)
        .where(IngestionJob.status == "pending")
        .order_by(IngestionJob.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def worker_loop(stop_event: asyncio.Event) -> None:
    logger.info("ingestion worker started")
    while not stop_event.is_set():
        try:
            async with SessionLocal() as db:
                job = await claim_one_job(db)
                if job is None:
                    await db.rollback()
                    await asyncio.sleep(settings.ingestion_worker_poll_seconds)
                    continue
                job_id = job.id
                await db.commit()
                logger.info("processing job %s", job_id)
                await run_job(db, job_id=job_id)
                logger.info("finished job %s", job_id)
        except Exception:
            logger.exception("worker iteration failed")
            await asyncio.sleep(settings.ingestion_worker_poll_seconds)

    logger.info("ingestion worker stopped")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            # Windows: signal handlers not supported on ProactorEventLoop
            pass

    await worker_loop(stop_event)


if __name__ == "__main__":
    asyncio.run(main())