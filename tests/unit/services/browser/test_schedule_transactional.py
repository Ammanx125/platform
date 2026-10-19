# tests/unit/services/browser/test_schedule_transactional.py
from __future__ import annotations

import asyncio

import pytest

from app.db.models.agent import Agent
from app.db.models.browser import BrowserExtractionRun
from app.db.models.dataset import DataSource
from app.db.session import SessionLocal
from app.schemas.browser import BrowserExtractionProfileCreate
from app.services.browser import service as svc
from app.services.browser.service import BrowserServiceError


async def _setup(db, tenant_id, user_id):
    src = DataSource(tenant_id=tenant_id, name="S", source_type="agent")
    db.add(src)
    await db.flush()
    db.add(Agent(tenant_id=tenant_id, name="ag", source_id=src.id, status="active"))
    await db.flush()
    auth = await svc.create_authorization(
        db, tenant_id=tenant_id, source_id=src.id, user_id=user_id,
        origin="https://erp.example.com",
        approved_purpose="x", scope={}, evidence_ref=None,
    )
    prof = await svc.create_profile(
        db, tenant_id=tenant_id, source_id=src.id, user_id=user_id,
        payload=BrowserExtractionProfileCreate(
            name="p", report_identifier="r",
            authorization_id=auth.id, expected_columns=["a"],
        ),
    )
    return prof


@pytest.mark.asyncio
async def test_concurrent_schedule_only_one_succeeds(two_tenants):
    """
    Two concurrent schedule_extraction calls against the same profile
    must result in exactly one run. The partial unique index
    uq_browser_runs_one_in_flight_per_profile is the hard guarantee; the
    friendly check is only a fast path.
    """
    ta, ua = two_tenants["tenant_a"], two_tenants["user_a"]

    async with SessionLocal() as db_setup:
        prof = await _setup(db_setup, ta, ua)
        await db_setup.commit()
        prof_id = prof.id

    async def _attempt():
        async with SessionLocal() as db:
            try:
                await svc.schedule_extraction(
                    db, tenant_id=ta, profile_id=prof_id, user_id=ua,
                )
                await db.commit()
                return "ok"
            except BrowserServiceError:
                await db.rollback()
                return "conflict"

    results = await asyncio.gather(_attempt(), _attempt())
    assert sorted(results) == ["conflict", "ok"]

    async with SessionLocal() as db:
        rows = (
            await db.execute(
                BrowserExtractionRun.__table__.select().where(
                    BrowserExtractionRun.profile_id == prof_id
                )
            )
        ).all()
        assert len(rows) == 1