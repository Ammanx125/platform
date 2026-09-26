# app/workers/tasks/triggers.py
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.events.dispatcher import dispatch_once


async def run_triggers_once(db: AsyncSession) -> int:
    return await dispatch_once(db, batch=10)