# app/workers/main.py
"""
The worker.

Run alongside the API:
    python -m app.workers.main

One process, N registered periodic tasks. Each tick iterates tasks in
order, runs any that are due, and logs the outcome. Failures in one task
never stop the others.

On SIGINT/SIGTERM (where supported), finishes the current task tick and
exits cleanly. On Windows the signal handlers aren't available on the
default event loop, so Ctrl+C is the manual equivalent.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import time

from app.db.session import SessionLocal
from app.workers.registry import get_tasks

logger = logging.getLogger("sansa.worker")


async def tick() -> None:
    """Run every task that's currently due."""
    now = time.monotonic()
    for task in get_tasks():
        if not task.is_due(now):
            continue
        t0 = time.perf_counter()
        try:
            async with SessionLocal() as db:
                processed = await task.run_once(db)
                await db.commit()
            task.last_error = None
            if processed:
                logger.info(
                    "task %s processed %d item(s)", task.name, processed
                )
        except Exception as exc:  # noqa: BLE001
            task.last_error = str(exc)[:300]
            logger.exception("task %s failed: %s", task.name, exc)
        finally:
            task.last_run_at = time.monotonic()
            task.last_duration_ms = int((time.perf_counter() - t0) * 1000)


async def worker_loop(stop_event: asyncio.Event) -> None:
    logger.info("worker started")
    # Shortest interval dictates the tick rate; run at half that so we
    # don't miss due times by much.
    min_interval = min((t.interval_seconds for t in get_tasks()), default=2.0)
    sleep_for = max(0.25, min_interval / 2.0)

    while not stop_event.is_set():
        try:
            await tick()
        except Exception:  # noqa: BLE001
            logger.exception("tick failed")
        await asyncio.sleep(sleep_for)

    logger.info("worker stopped")


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    await worker_loop(stop_event)


if __name__ == "__main__":
    asyncio.run(main())