# app/workers/tasks/browser_reaper.py
"""
Reaper for stale browser extraction runs.

Runs periodically. Fails any run stuck in an in-flight state past its
deadline, releasing the in-flight slot for the profile so a scheduled
extraction isn't blocked forever by an offline agent.

Registered with the worker's task registry. The task is idempotent: a
run that has already transitioned out of an in-flight state is left
alone.
"""
from __future__ import annotations

import logging

from app.db.session import SessionLocal
from app.services.browser import service as browser_service

logger = logging.getLogger("sansa.workers.browser_reaper")


async def reap_browser_runs() -> int:
    """
    Sweep all tenants. Returns the number of runs reaped.
    """
    async with SessionLocal() as db:
        try:
            count = await browser_service.reap_stale_runs(db)
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("browser reaper failed")
            return 0
    if count:
        logger.info("reaped %d stale browser run(s)", count)
    return count