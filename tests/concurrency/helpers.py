# tests/concurrency/helpers.py
"""
Shared utilities for concurrency tests.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.services.workflows.base import Step
from app.services.workflows.registry import get as get_workflow
from app.services.workflows.registry import register

CONCURRENCY_APPROVAL_WORKFLOW_KEY = "tests.concurrency.approval"


class _ConcurrencyApprovalWorkflow:
    key = CONCURRENCY_APPROVAL_WORKFLOW_KEY
    display_name = "Concurrency Approval Test"
    description = "A minimal workflow for approval race tests."
    domain = "test"
    trigger_keywords = frozenset()
    requirements: list = []
    steps = [
        Step(name="before", type="set_context", params={"updates": {"before": True}}),
        Step(name="approval", type="await_approval", params={}),
        Step(name="after", type="set_context", params={"updates": {"after": True}}),
    ]


def waiting_approval_step_states() -> list[dict[str, Any]]:
    workflow = get_workflow(CONCURRENCY_APPROVAL_WORKFLOW_KEY)
    if workflow is None:
        register(_ConcurrencyApprovalWorkflow())
        workflow = get_workflow(CONCURRENCY_APPROVAL_WORKFLOW_KEY)
    if workflow is None:
        raise RuntimeError("concurrency approval workflow failed to register")

    statuses = ["completed", "waiting", "pending"]
    return [
        {
            "name": step.name,
            "type": step.type,
            "status": statuses[index],
            "output": {},
            "started_at": None,
            "finished_at": None,
            "error": None,
        }
        for index, step in enumerate(workflow.steps)
    ]


@dataclass
class RaceResult:
    """
    The outcome of a single contender in a race.

    Either `value` is set (the coroutine returned successfully) or
    `exception` is set (it raised).
    """
    value: Any = None
    exception: BaseException | None = None

    @property
    def succeeded(self) -> bool:
        return self.exception is None


async def run_concurrently(
    coros: list[Callable[[], Awaitable[Any]]],
) -> list[RaceResult]:
    """
    Launch all coroutines "at the same time" via asyncio.gather, and
    capture each result or exception without letting one failure abort
    the others.

    Note: asyncio.gather schedules them concurrently but not in parallel;
    true DB contention comes from separate sessions and separate
    transactions. The `pg_sleep` inside a locked window (see tests) is
    what forces an actual interleaving on the DB side.
    """
    async def _wrap(fn: Callable[[], Awaitable[Any]]) -> RaceResult:
        try:
            return RaceResult(value=await fn())
        except BaseException as exc:  # noqa: BLE001
            return RaceResult(exception=exc)

    tasks = [asyncio.create_task(_wrap(c)) for c in coros]
    return await asyncio.gather(*tasks)


def count_successes(results: list[RaceResult]) -> int:
    return sum(1 for r in results if r.succeeded)


def count_failures(results: list[RaceResult]) -> int:
    return sum(1 for r in results if not r.succeeded)