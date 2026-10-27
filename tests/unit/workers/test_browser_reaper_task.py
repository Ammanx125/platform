# tests/unit/workers/test_browser_reaper_task.py
from __future__ import annotations

import pytest

from app.workers.tasks.browser_reaper import reap_browser_runs


@pytest.mark.asyncio
async def test_reaper_runs_cleanly_with_nothing_to_do(two_tenants):
    # Ensure importing and running the task doesn't explode on an empty DB.
    n = await reap_browser_runs()
    assert isinstance(n, int)
    assert n >= 0