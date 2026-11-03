# app/workers/registry.py
"""
Registered worker tasks.

The order here is the order the loop ticks them. Keep high-frequency,
low-cost tasks (ingestion) early and lower-frequency tasks later.
"""
from __future__ import annotations

from app.workers.base import Task
from app.workers.tasks.browser_reaper import run_browser_reaper_once
from app.workers.tasks.ingestion import run_ingestion_once
from app.workers.tasks.triggers import run_triggers_once
from app.workers.tasks.webhooks import run_webhooks_once
from app.workers.tasks.workflows import (
    run_pending_workflows_once,
    run_waiting_workflows_once,
)

TASKS: list[Task] = [
    Task("ingestion", 2.0, run_ingestion_once),
    Task("webhooks", 2.0, run_webhooks_once),
    Task("workflows_pending", 2.0, run_pending_workflows_once),
    Task("workflows_resume", 2.0, run_waiting_workflows_once),
    Task("triggers", 3.0, run_triggers_once),
    Task("browser_reaper", 60.0, run_browser_reaper_once),
]


def get_tasks() -> list[Task]:
    return TASKS