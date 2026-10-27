from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.db.models.agent import Agent, AgentJob
from app.db.models.browser import (
    BrowserAuthorization,
    BrowserExtractionProfile,
    BrowserExtractionRun,
)
from app.db.models.dataset import DataSource, IngestionJob
from app.db.session import SessionLocal
from app.services.browser.activation import (
    maybe_activate_snapshot,
    maybe_fail_browser_run_on_ingestion_failure,
)


@pytest.mark.asyncio
async def test_ingestion_job_activates_browser_snapshot_and_supersedes_previous(
    two_tenants,
):
    tenant_id = two_tenants["tenant_a"]
    user_id = two_tenants["user_a"]

    async with SessionLocal() as db:
        source = DataSource(
            tenant_id=tenant_id,
            name="browser-source",
            source_type="agent",
            snapshot_mode="replace_active",
        )
        db.add(source)
        await db.flush()

        previous_job = IngestionJob(
            tenant_id=tenant_id, source_id=source.id, status="succeeded"
        )
        ingestion_job = IngestionJob(
            tenant_id=tenant_id, source_id=source.id, status="succeeded"
        )
        db.add_all([previous_job, ingestion_job])
        await db.flush()

        authorization = BrowserAuthorization(
            tenant_id=tenant_id,
            source_id=source.id,
            authorized_by_user_id=user_id,
            origin="https://erp.example.com",
            approved_purpose="test",
            scope={},
            status="active",
        )
        db.add(authorization)
        await db.flush()

        profile = BrowserExtractionProfile(
            tenant_id=tenant_id,
            source_id=source.id,
            authorization_id=authorization.id,
            name="daily-report",
            report_identifier="Daily report",
            extraction_instructions={},
            expected_columns=["amount"],
            limits={},
            snapshot_mode="replace_active",
            active_ingestion_job_id=previous_job.id,
        )
        db.add(profile)

        agent = Agent(
            tenant_id=tenant_id,
            name="browser-agent",
            source_id=source.id,
            status="active",
        )
        db.add(agent)
        await db.flush()

        previous_agent_job = AgentJob(
            tenant_id=tenant_id,
            agent_id=agent.id,
            job_type="browser_extract",
            params={},
        )
        new_agent_job = AgentJob(
            tenant_id=tenant_id,
            agent_id=agent.id,
            job_type="browser_extract",
            params={},
        )
        db.add_all([previous_agent_job, new_agent_job])
        await db.flush()

        previous_run = BrowserExtractionRun(
            tenant_id=tenant_id,
            profile_id=profile.id,
            agent_job_id=previous_agent_job.id,
            ingestion_job_id=previous_job.id,
            run_id="previous",
            status="active",
            actual_sha256="a" * 64,
            activated_at=datetime.now(UTC),
        )
        new_run = BrowserExtractionRun(
            tenant_id=tenant_id,
            profile_id=profile.id,
            agent_job_id=new_agent_job.id,
            ingestion_job_id=ingestion_job.id,
            run_id="current",
            status="activating",
            actual_sha256="b" * 64,
        )
        db.add_all([previous_run, new_run])
        await db.flush()

        activated = await maybe_activate_snapshot(db, ingestion_job=ingestion_job)

        assert activated is new_run
        assert profile.active_ingestion_job_id == ingestion_job.id
        assert new_run.status == "active"
        assert new_run.activated_at is not None
        assert previous_run.status == "superseded"
        assert previous_run.superseded_at is not None


@pytest.mark.asyncio
async def test_ingestion_without_browser_run_does_not_change_source(two_tenants):
    tenant_id = two_tenants["tenant_a"]

    async with SessionLocal() as db:
        source = DataSource(
            tenant_id=tenant_id,
            name="ordinary-source",
            source_type="csv",
        )
        db.add(source)
        await db.flush()
        ingestion_job = IngestionJob(
            tenant_id=tenant_id,
            source_id=source.id,
            status="succeeded",
        )
        db.add(ingestion_job)
        await db.flush()

        activated = await maybe_activate_snapshot(db, ingestion_job=ingestion_job)

        assert activated is None
        assert source.snapshot_mode == "append"


@pytest.mark.asyncio
async def test_failed_ingestion_fails_browser_run_without_swapping_snapshot(
    two_tenants,
):
    tenant_id = two_tenants["tenant_a"]
    user_id = two_tenants["user_a"]

    async with SessionLocal() as db:
        source = DataSource(
            tenant_id=tenant_id,
            name="browser-source",
            source_type="agent",
            snapshot_mode="replace_active",
        )
        db.add(source)
        await db.flush()

        active_job = IngestionJob(
            tenant_id=tenant_id, source_id=source.id, status="succeeded"
        )
        failed_job = IngestionJob(
            tenant_id=tenant_id,
            source_id=source.id,
            status="failed",
            error_message="CSV parsing failed",
        )
        db.add_all([active_job, failed_job])
        await db.flush()

        authorization = BrowserAuthorization(
            tenant_id=tenant_id,
            source_id=source.id,
            authorized_by_user_id=user_id,
            origin="https://erp.example.com",
            approved_purpose="test",
            scope={},
            status="active",
        )
        db.add(authorization)
        await db.flush()

        profile = BrowserExtractionProfile(
            tenant_id=tenant_id,
            source_id=source.id,
            authorization_id=authorization.id,
            name="daily-report",
            report_identifier="Daily report",
            extraction_instructions={},
            expected_columns=["amount"],
            limits={},
            snapshot_mode="replace_active",
            active_ingestion_job_id=active_job.id,
        )
        db.add(profile)

        agent = Agent(
            tenant_id=tenant_id,
            name="browser-agent",
            source_id=source.id,
            status="active",
        )
        db.add(agent)
        await db.flush()
        agent_job = AgentJob(
            tenant_id=tenant_id,
            agent_id=agent.id,
            job_type="browser_extract",
            params={},
        )
        db.add(agent_job)
        await db.flush()

        run = BrowserExtractionRun(
            tenant_id=tenant_id,
            profile_id=profile.id,
            agent_job_id=agent_job.id,
            ingestion_job_id=failed_job.id,
            run_id="failed-ingestion",
            status="activating",
        )
        db.add(run)
        await db.flush()

        failed = await maybe_fail_browser_run_on_ingestion_failure(
            db, ingestion_job=failed_job
        )

        assert failed is run
        assert run.status == "failed"
        assert run.error == "ingestion failed: CSV parsing failed"
        assert profile.active_ingestion_job_id == active_job.id
