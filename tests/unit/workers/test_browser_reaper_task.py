# tests/unit/workers/test_browser_reaper_task.py
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings
from app.db.models.agent import Agent
from app.db.models.dataset import DataSource
from app.db.session import SessionLocal
from app.schemas.browser import BrowserExtractionProfileCreate
from app.services.browser import service as svc
from app.workers.tasks.browser_reaper import run_browser_reaper_once


@pytest.mark.asyncio
async def test_reaper_runs_with_nothing_to_do(two_tenants):
    async with SessionLocal() as db:
        n = await run_browser_reaper_once(db)
        await db.commit()
        assert n == 0


@pytest.mark.asyncio
async def test_reaper_fails_expired_run(two_tenants):
    ta, ua = two_tenants["tenant_a"], two_tenants["user_a"]
    async with SessionLocal() as db:
        src = DataSource(tenant_id=ta, name="S", source_type="agent")
        db.add(src)
        await db.flush()
        agent = Agent(tenant_id=ta, name="ag", source_id=src.id, status="active")
        db.add(agent)
        await db.flush()
        auth = await svc.create_authorization(
            db, tenant_id=ta, source_id=src.id, user_id=ua,
            origin="https://erp.example.com",
            approved_purpose="x", scope={}, evidence_ref=None,
        )
        prof = await svc.create_profile(
            db, tenant_id=ta, source_id=src.id, user_id=ua,
            payload=BrowserExtractionProfileCreate(
                name="p", report_identifier="r",
                authorization_id=auth.id, expected_columns=["date", "amount"],
            ),
        )
        await db.flush()
        job, run = await svc.schedule_extraction(
            db, tenant_id=ta, profile_id=prof.id, user_id=ua,
        )
        await db.commit()

        # Force the run to look old and stuck.
        run.status = "extracting"
        run.created_at = datetime.now(UTC) - timedelta(
            seconds=settings.browser_run_timeout_seconds + 60
        )
        await db.commit()

        async with SessionLocal() as db2:
            n = await run_browser_reaper_once(db2)
            await db2.commit()
        assert n == 1

        await db.refresh(run)
        assert run.status == "failed"
        assert run.error == "run timed out"