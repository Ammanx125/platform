# tests/unit/services/browser/test_reap_stale_runs.py
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings
from app.db.models.browser import BrowserExtractionRun
from app.db.session import SessionLocal
from app.services.browser import service as svc


async def _make_in_flight_run(db, *, tenant_id, profile_id, agent_job_id, run_id, status):
    run = BrowserExtractionRun(
        tenant_id=tenant_id, profile_id=profile_id, agent_job_id=agent_job_id,
        run_id=run_id, status=status,
    )
    db.add(run)
    await db.flush()
    return run


@pytest.mark.asyncio
async def test_reap_marks_old_in_flight_runs_failed(two_tenants):
    from app.db.models.agent import Agent, AgentJob
    from app.db.models.dataset import DataSource
    from app.schemas.browser import BrowserExtractionProfileCreate
    from app.services.browser import service as svc

    ta, ua = two_tenants["tenant_a"], two_tenants["user_a"]
    async with SessionLocal() as db:
        src = DataSource(tenant_id=ta, name="S", source_type="agent")
        db.add(src); await db.flush()
        db.add(Agent(tenant_id=ta, name="ag", source_id=src.id, status="active"))
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
                authorization_id=auth.id, expected_columns=["a"],
            ),
        )
        await db.flush()

        job1 = AgentJob(
            tenant_id=ta, agent_id=(await db.execute(
                __import__("sqlalchemy").select(Agent).where(Agent.tenant_id == ta)
            )).scalar_one().id,
            job_type="browser_extract", params={}, status="in_progress",
        )
        db.add(job1); await db.flush()

        old_run = await _make_in_flight_run(
            db, tenant_id=ta, profile_id=prof.id, agent_job_id=job1.id,
            run_id="old", status="extracting",
        )
        old_run.created_at = datetime.now(UTC) - timedelta(
            seconds=settings.browser_run_timeout_seconds + 60
        )
        await db.commit()

        n = await svc.reap_stale_runs(db, tenant_id=ta)
        await db.commit()
        assert n == 1

        await db.refresh(old_run)
        assert old_run.status == "failed"
        assert old_run.error == "run timed out"


@pytest.mark.asyncio
async def test_reap_leaves_fresh_runs_and_terminal_states_alone(two_tenants):
    from app.db.models.agent import Agent, AgentJob
    from app.db.models.dataset import DataSource
    from app.schemas.browser import BrowserExtractionProfileCreate

    ta, ua = two_tenants["tenant_a"], two_tenants["user_a"]
    async with SessionLocal() as db:
        src = DataSource(tenant_id=ta, name="S2", source_type="agent")
        db.add(src); await db.flush()
        agent = Agent(tenant_id=ta, name="ag2", source_id=src.id, status="active")
        db.add(agent); await db.flush()
        auth = await svc.create_authorization(
            db, tenant_id=ta, source_id=src.id, user_id=ua,
            origin="https://erp.example.com",
            approved_purpose="x", scope={}, evidence_ref=None,
        )
        prof = await svc.create_profile(
            db, tenant_id=ta, source_id=src.id, user_id=ua,
            payload=BrowserExtractionProfileCreate(
                name="p2", report_identifier="r",
                authorization_id=auth.id, expected_columns=["a"],
            ),
        )
        await db.flush()

        job = AgentJob(
            tenant_id=ta, agent_id=agent.id, job_type="browser_extract",
            params={}, status="in_progress",
        )
        db.add(job); await db.flush()

        fresh = await _make_in_flight_run(
            db, tenant_id=ta, profile_id=prof.id, agent_job_id=job.id,
            run_id="fresh", status="extracting",
        )
        # Not touched by reaper.

        # Also test: an old run in a terminal state is not touched.
        await db.commit()
        n = await svc.reap_stale_runs(db, tenant_id=ta)
        await db.commit()
        assert n == 0

        await db.refresh(fresh)
        assert fresh.status == "extracting"