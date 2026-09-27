# app/workers/jobs.py
"""Deprecated entrypoint; use ``python -m app.workers.main`` instead."""
from __future__ import annotations

import asyncio
import logging

from app.workers.main import main as worker_main

if __name__ == "__main__":
    logging.getLogger("sansa.worker").warning(
        "app.workers.jobs is deprecated; use app.workers.main"
    )
    asyncio.run(worker_main())