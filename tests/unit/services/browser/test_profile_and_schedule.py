# tests/unit/services/browser/test_profile_and_schedule.py
from __future__ import annotations

import pytest

from app.db.models.agent import Agent
from app.db.models.browser import BrowserExtractionRun
from app.db.models.dataset import DataSource
from app.db.session import SessionLocal
from app.schemas.browser import BrowserExtractionProfileCreate
from app.services.browser import service as svc
from app.services.browser.service import BrowserServiceError


async def _agent_source_with_agent(db, tenant_id, name="S"):
    src = DataSource(tenant_id=tenant_id, name=name, source_type="agent")
    db.add(src)
    await db.flush()
    agent = Agent(
        tenant_id=tenant_id, name=f"ag-{name}",
        source_id=src.id, status="active",
    )
    db.add(agent)
    await db.flush()
    return src, agent


async def _active_auth(db, tenant_id, source_id, user_id):
    a = await svc.create_authorization(
        db, tenant_id=tenant_id, source_id=source_id, user_id=user_id,
        origin="https://erp.example.com",
        approved_purpose="x", scope={}, evidence_ref=None,
    )
    return a


@pytest.mark.asyncio
async def test_create_profile_flips_source_snapshot_mode(two_tenants):
    ta, ua = two_tenants["tenant_a"], two_tenants["user_a"]
    async with SessionLocal() as db:
        src, _ = await _agent_source_with_agent(db, ta)
        auth = await _active_auth(db, ta, src.id, ua)
        payload = BrowserExtractionProfileCreate(
            name="p1", report_identifier="Sales > Daily",
            authorization_id=auth.id,
            expected_columns=["date", "amount"],
        )
        prof = await svc.create_profile(
            db, tenant_id=ta, source_id=src.id, user_id=ua, payload=payload,
        )
        await db.commit()
        await db.refresh(src)
        assert prof.revision == 1
        assert src.snapshot_mode == "replace_active"


@pytest.mark.asyncio
async def test_create_profile_refuses_revoked_authorization(two_tenants):
    ta, ua = two_tenants["tenant_a"], two_tenants["user_a"]
    async with SessionLocal() as db:
        src, _ = await _agent_source_with_agent(db, ta)
        auth = await _active_auth(db, ta, src.id, ua)
        await svc.revoke_authorization(
            db, tenant_id=ta, authorization_id=auth.id, user_id=ua,
        )
        await db.flush()
        payload = BrowserExtractionProfileCreate(
            name="p1", report_identifier="r",
            authorization_id=auth.id, expected_columns=["a"],
        )
        with pytest.raises(BrowserServiceError):
            await svc.create_profile(
                db, tenant_id=ta, source_id=src.id, user_id=ua, payload=payload,
            )


@pytest.mark.asyncio
async def test_create_profile_refuses_second_for_same_source(two_tenants):
    ta, ua = two_tenants["tenant_a"], two_tenants["user_a"]
    async with SessionLocal() as db:
        src, _ = await _agent_source_with_agent(db, ta)
        auth = await _active_auth(db, ta, src.id, ua)
        p = BrowserExtractionProfileCreate(
            name="p1", report_identifier="r",
            authorization_id=auth.id, expected_columns=["a"],
        )
        await svc.create_profile(db, tenant_id=ta, source_id=src.id, user_id=ua, payload=p)
        await db.flush()
        p2 = BrowserExtractionProfileCreate(
            name="p2", report_identifier="r2",
            authorization_id=auth.id, expected_columns=["a"],
        )
        with pytest.raises(BrowserServiceError):
            await svc.create_profile(
                db, tenant_id=ta, source_id=src.id, user_id=ua, payload=p2,
            )


@pytest.mark.asyncio
async def test_schedule_refuses_when_authorization_revoked_before_schedule(two_tenants):
    ta, ua = two_tenants["tenant_a"], two_tenants["user_a"]
    async with SessionLocal() as db:
        src, _ = await _agent_source_with_agent(db, ta)
        auth = await _active_auth(db, ta, src.id, ua)
        prof = await svc.create_profile(
            db, tenant_id=ta, source_id=src.id, user_id=ua,
            payload=BrowserExtractionProfileCreate(
                name="p1", report_identifier="r",
                authorization_id=auth.id, expected_columns=["a"],
            ),
        )
        await db.flush()
        await svc.revoke_authorization(
            db, tenant_id=ta, authorization_id=auth.id, user_id=ua,
        )
        await db.flush()
        with pytest.raises(BrowserServiceError):
            await svc.schedule_extraction(
                db, tenant_id=ta, profile_id=prof.id, user_id=ua,
            )


@pytest.mark.asyncio
async def test_schedule_creates_job_and_run_with_server_owned_run_id(two_tenants):
    ta, ua = two_tenants["tenant_a"], two_tenants["user_a"]
    async with SessionLocal() as db:
        src, agent = await _agent_source_with_agent(db, ta)
        auth = await _active_auth(db, ta, src.id, ua)
        prof = await svc.create_profile(
            db, tenant_id=ta, source_id=src.id, user_id=ua,
            payload=BrowserExtractionProfileCreate(
                name="p1", report_identifier="Sales > Daily",
                authorization_id=auth.id, expected_columns=["date", "amount"],
            ),
        )
        await db.commit()

        job, run = await svc.schedule_extraction(
            db, tenant_id=ta, profile_id=prof.id, user_id=ua,
        )
        await db.commit()

        assert job.job_type == "browser_extract"
        assert job.agent_id == agent.id
        assert job.params["run_id"] == run.run_id
        assert job.params["profile_id"] == str(prof.id)
        assert job.params["approved_origin"] == "https://erp.example.com"
        assert job.params["schema_version"] == 1
        assert run.status == "scheduled"
        assert run.agent_job_id == job.id


@pytest.mark.asyncio
async def test_schedule_refuses_when_run_in_flight(two_tenants):
    ta, ua = two_tenants["tenant_a"], two_tenants["user_a"]
    async with SessionLocal() as db:
        src, _ = await _agent_source_with_agent(db, ta)
        auth = await _active_auth(db, ta, src.id, ua)
        prof = await svc.create_profile(
            db, tenant_id=ta, source_id=src.id, user_id=ua,
            payload=BrowserExtractionProfileCreate(
                name="p1", report_identifier="r",
                authorization_id=auth.id, expected_columns=["a"],
            ),
        )
        await db.commit()

        await svc.schedule_extraction(
            db, tenant_id=ta, profile_id=prof.id, user_id=ua,
        )
        await db.commit()

        with pytest.raises(BrowserServiceError):
            await svc.schedule_extraction(
                db, tenant_id=ta, profile_id=prof.id, user_id=ua,
            )