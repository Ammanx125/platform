# app/workers/tasks/browser_reaper.py
"""
Reaper for stale browser extraction runs.

Runs periodically. Fails any run stuck in an in-flight state past its
deadline, releasing the in-flight slot for the profile so a scheduled
extraction isn't blocked forever by an offline agent.

Idempotent: a run that has already transitioned out of an in-flight
state is left alone, and re-running the task on the next tick will not
re-fail anything.
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.browser import service as browser_service

logger = logging.getLogger("sansa.worker.browser_reaper")


async def run_browser_reaper_once(db: AsyncSession) -> int:
    """
    Sweep all tenants once. Returns the number of runs reaped, which the
    worker loop uses for its log line.
    """
    count = await browser_service.reap_stale_runs(db)
    if count:
        logger.info("reaped %d stale browser run(s)", count)
    return count