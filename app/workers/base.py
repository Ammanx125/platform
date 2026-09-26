# app/workers/base.py
"""
Worker task contract.

A Task is a named periodic unit of work. The worker loop iterates
registered tasks; for each, if its interval has elapsed, it calls
`run_once(db)` in a fresh session.

run_once returns the number of items processed. The loop uses this for
logging only; a return of 0 means "nothing to do," which is normal.

Tasks must be idempotent on re-entry: a task that fails partway is
re-run on the next tick, and it must not double-process items it already
handled. In practice, this is achieved by the task claiming work with
`FOR UPDATE SKIP LOCKED` and marking rows with a status transition.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class Task:
    name: str
    interval_seconds: float
    run_once: Callable[[AsyncSession], Awaitable[int]]
    enabled: bool = True
    # Runtime bookkeeping; the loop updates these.
    last_run_at: float = field(default=0.0)
    last_duration_ms: int = field(default=0)
    last_error: str | None = None

    def is_due(self, now: float) -> bool:
        if not self.enabled:
            return False
        return (now - self.last_run_at) >= self.interval_seconds