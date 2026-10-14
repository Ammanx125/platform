# tests/unit/models/test_browser_constraints.py
from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models.agent import Agent, AgentJob
from app.db.models.browser import (
    BrowserAuthorization,
    BrowserExtractionProfile,
    BrowserExtractionRun,
)
from app.db.models.dataset import DataSource
from app.db.session import SessionLocal


async def _make_source_and_agent(db, *, tenant_id, name="Browser Src"):
    src = DataSource(
        tenant_id=tenant_id, name=name, source_type="agent",
    )
    db.add(src)
    await db.flush()
    agent = Agent(
        tenant_id=tenant_id,
        name=f"agent-{name}",
        source_id=src.id,
        status="active",
    )
    db.add(agent)
    await db.flush()
    return src, agent


async def _make_authorization(db, *, tenant_id, source_id, user_id=None):
    auth = BrowserAuthorization(
        tenant_id=tenant_id,
        source_id=source_id,
        authorized_by_user_id=user_id,
        origin="https://erp.example.com",
        approved_purpose="monthly sales report",
        scope={"report_patterns": ["Sales > Daily"]},
        status="active",
    )
    db.add(auth)
    await db.flush()
    return auth


async def _make_profile(db, *, tenant_id, source_id, auth_id, name="p1"):
    prof = BrowserExtractionProfile(
        tenant_id=tenant_id,
        source_id=source_id,
        authorization_id=auth_id,
        name=name,
        report_identifier="Sales > Daily",
        extraction_instructions={"steps": []},
        expected_columns=["date", "amount"],
        limits={"max_rows": 1000},
        snapshot_mode="replace_active",
    )
    db.add(prof)
    await db.flush()
    return prof


@pytest.mark.asyncio
async def test_unique_profile_name_per_tenant(two_tenants):
    ta = two_tenants["tenant_a"]
    async with SessionLocal() as db:
        src, _ = await _make_source_and_agent(db, tenant_id=ta, name="A1")
        auth = await _make_authorization(db, tenant_id=ta, source_id=src.id)
        await _make_profile(db, tenant_id=ta, source_id=src.id, auth_id=auth.id, name="same")
        await db.commit()

        src2, _ = await _make_source_and_agent(db, tenant_id=ta, name="A2")
        auth2 = await _make_authorization(db, tenant_id=ta, source_id=src2.id)
        with pytest.raises(IntegrityError):
            await _make_profile(
                db, tenant_id=ta, source_id=src2.id,
                auth_id=auth2.id, name="same",
            )
            await db.commit()


@pytest.mark.asyncio
async def test_one_profile_per_source(two_tenants):
    ta = two_tenants["tenant_a"]
    async with SessionLocal() as db:
        src, _ = await _make_source_and_agent(db, tenant_id=ta, name="B1")
        auth = await _make_authorization(db, tenant_id=ta, source_id=src.id)
        await _make_profile(db, tenant_id=ta, source_id=src.id, auth_id=auth.id, name="p1")
        await db.commit()

        with pytest.raises(IntegrityError):
            await _make_profile(
                db, tenant_id=ta, source_id=src.id,
                auth_id=auth.id, name="p2",
            )
            await db.commit()


@pytest.mark.asyncio
async def test_run_unique_on_agent_job(two_tenants):
    ta = two_tenants["tenant_a"]
    async with SessionLocal() as db:
        src, agent = await _make_source_and_agent(db, tenant_id=ta, name="C1")
        auth = await _make_authorization(db, tenant_id=ta, source_id=src.id)
        prof = await _make_profile(db, tenant_id=ta, source_id=src.id, auth_id=auth.id)
        job = AgentJob(
            tenant_id=ta, agent_id=agent.id,
            job_type="browser_extract", params={}, status="pending",
        )
        db.add(job)
        await db.flush()

        db.add(BrowserExtractionRun(
            tenant_id=ta, profile_id=prof.id, agent_job_id=job.id,
            run_id="r1", status="scheduled",
        ))
        await db.flush()

        with pytest.raises(IntegrityError):
            db.add(BrowserExtractionRun(
                tenant_id=ta, profile_id=prof.id, agent_job_id=job.id,
                run_id="r2", status="scheduled",
            ))
            await db.commit()


@pytest.mark.asyncio
async def test_run_unique_on_tenant_profile_runid(two_tenants):
    ta = two_tenants["tenant_a"]
    async with SessionLocal() as db:
        src, agent = await _make_source_and_agent(db, tenant_id=ta, name="D1")
        auth = await _make_authorization(db, tenant_id=ta, source_id=src.id)
        prof = await _make_profile(db, tenant_id=ta, source_id=src.id, auth_id=auth.id)

        j1 = AgentJob(
            tenant_id=ta, agent_id=agent.id,
            job_type="browser_extract", params={}, status="pending",
        )
        j2 = AgentJob(
            tenant_id=ta, agent_id=agent.id,
            job_type="browser_extract", params={}, status="pending",
        )
        db.add_all([j1, j2])
        await db.flush()

        db.add(BrowserExtractionRun(
            tenant_id=ta, profile_id=prof.id, agent_job_id=j1.id,
            run_id="same", status="scheduled",
        ))
        await db.flush()

        with pytest.raises(IntegrityError):
            db.add(BrowserExtractionRun(
                tenant_id=ta, profile_id=prof.id, agent_job_id=j2.id,
                run_id="same", status="scheduled",
            ))
            await db.commit()


@pytest.mark.asyncio
async def test_datasource_snapshot_mode_default(two_tenants):
    ta = two_tenants["tenant_a"]
    async with SessionLocal() as db:
        src = DataSource(tenant_id=ta, name="Default", source_type="csv")
        db.add(src)
        await db.commit()
        await db.refresh(src)
        assert src.snapshot_mode == "append"