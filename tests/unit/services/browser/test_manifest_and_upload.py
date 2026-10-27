# tests/unit/services/browser/test_manifest_and_upload.py
from __future__ import annotations

import hashlib

import pytest

from app.db.models.agent import Agent, AgentJob
from app.db.models.browser import BrowserExtractionRun
from app.db.models.dataset import DataSource, IngestionJob
from app.db.session import SessionLocal
from app.schemas.browser import (
    BrowserExtractionJobParams,
    BrowserExtractionManifest,
    BrowserExtractionPaginationSpec,
    BrowserExtractionProfileCreate,
    BrowserExtractionReportSpec,
)
from app.services.browser import service as svc
from app.services.browser.service import BrowserServiceError


async def _setup_ready_to_extract(db, tenant_id, user_id):
    """
    Create a source + agent + active auth + profile, then schedule an
    extraction. Returns (profile, agent, agent_job, run).
    """
    src = DataSource(tenant_id=tenant_id, name="S", source_type="agent")
    db.add(src)
    await db.flush()

    agent = Agent(
        tenant_id=tenant_id, name="ag", source_id=src.id, status="active",
    )
    db.add(agent)
    await db.flush()

    auth = await svc.create_authorization(
        db, tenant_id=tenant_id, source_id=src.id, user_id=user_id,
        origin="https://erp.example.com",
        approved_purpose="x", scope={}, evidence_ref=None,
    )
    prof = await svc.create_profile(
        db, tenant_id=tenant_id, source_id=src.id, user_id=user_id,
        payload=BrowserExtractionProfileCreate(
            name="p", report_identifier="Sales > Daily",
            authorization_id=auth.id,
            expected_columns=["date", "amount"],
        ),
    )
    await db.flush()

    job, run = await svc.schedule_extraction(
        db, tenant_id=tenant_id, profile_id=prof.id, user_id=user_id,
    )
    await db.commit()
    return prof, agent, job, run


def _csv_bytes(rows: list[tuple[str, str]], headers=("date", "amount")) -> bytes:
    buf = ",".join(headers) + "\n"
    for d, a in rows:
        buf += f"{d},{a}\n"
    return buf.encode("utf-8")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@pytest.mark.asyncio
async def test_manifest_happy_path(two_tenants):
    ta, ua = two_tenants["tenant_a"], two_tenants["user_a"]
    async with SessionLocal() as db:
        _prof, _agent, job, run = await _setup_ready_to_extract(db, ta, ua)
        content = _csv_bytes([("2026-01-01", "10"), ("2026-01-02", "20")])
        manifest = BrowserExtractionManifest(
            run_id=run.run_id,
            sha256=_sha256(content),
            row_count=2,
            expected_columns=["date", "amount"],
            complete=True,
        )
        updated = await svc.handle_manifest(
            db, agent_job=job, manifest=manifest, agent_tenant_id=ta,
        )
        await db.commit()
        assert updated.status == "manifest_received"
        assert updated.expected_sha256 == manifest.sha256
        assert updated.expected_row_count == 2


@pytest.mark.asyncio
async def test_manifest_wrong_run_id_rejected(two_tenants):
    ta, ua = two_tenants["tenant_a"], two_tenants["user_a"]
    async with SessionLocal() as db:
        _prof, _agent, job, _run = await _setup_ready_to_extract(db, ta, ua)
        manifest = BrowserExtractionManifest(
            run_id="wrong-run-id",
            sha256="a" * 64,
            row_count=2,
            expected_columns=["date", "amount"],
            complete=True,
        )
        with pytest.raises(BrowserServiceError):
            await svc.handle_manifest(
                db, agent_job=job, manifest=manifest, agent_tenant_id=ta,
            )


@pytest.mark.asyncio
async def test_manifest_wrong_tenant_rejected(two_tenants):
    ta, ua = two_tenants["tenant_a"], two_tenants["user_a"]
    tb = two_tenants["tenant_b"]
    async with SessionLocal() as db:
        _prof, _agent, job, run = await _setup_ready_to_extract(db, ta, ua)
        manifest = BrowserExtractionManifest(
            run_id=run.run_id,
            sha256="a" * 64,
            row_count=2,
            expected_columns=["date", "amount"],
            complete=True,
        )
        with pytest.raises(BrowserServiceError):
            await svc.handle_manifest(
                db, agent_job=job, manifest=manifest, agent_tenant_id=tb,
            )


@pytest.mark.asyncio
async def test_manifest_replay_same_sha_is_idempotent(two_tenants):
    ta, ua = two_tenants["tenant_a"], two_tenants["user_a"]
    async with SessionLocal() as db:
        _prof, _agent, job, run = await _setup_ready_to_extract(db, ta, ua)
        content = _csv_bytes([("2026-01-01", "10")])
        manifest = BrowserExtractionManifest(
            run_id=run.run_id,
            sha256=_sha256(content),
            row_count=1,
            expected_columns=["date", "amount"],
            complete=True,
        )
        await svc.handle_manifest(
            db, agent_job=job, manifest=manifest, agent_tenant_id=ta,
        )
        await db.commit()

        # Replay same manifest.
        again = await svc.handle_manifest(
            db, agent_job=job, manifest=manifest, agent_tenant_id=ta,
        )
        await db.commit()
        assert again.status == "manifest_received"


@pytest.mark.asyncio
async def test_manifest_replay_different_sha_conflict(two_tenants):
    ta, ua = two_tenants["tenant_a"], two_tenants["user_a"]
    async with SessionLocal() as db:
        _prof, _agent, job, run = await _setup_ready_to_extract(db, ta, ua)
        m1 = BrowserExtractionManifest(
            run_id=run.run_id,
            sha256="a" * 64,
            row_count=1,
            expected_columns=["date", "amount"],
            complete=True,
        )
        await svc.handle_manifest(
            db, agent_job=job, manifest=m1, agent_tenant_id=ta,
        )
        await db.commit()

        m2 = BrowserExtractionManifest(
            run_id=run.run_id,
            sha256="b" * 64,
            row_count=1,
            expected_columns=["date", "amount"],
            complete=True,
        )
        with pytest.raises(BrowserServiceError):
            await svc.handle_manifest(
                db, agent_job=job, manifest=m2, agent_tenant_id=ta,
            )


@pytest.mark.asyncio
async def test_manifest_refuses_when_authorization_revoked(two_tenants):
    ta, ua = two_tenants["tenant_a"], two_tenants["user_a"]
    async with SessionLocal() as db:
        prof, _agent, job, run = await _setup_ready_to_extract(db, ta, ua)
        # Revoke the profile's authorization.
        await svc.revoke_authorization(
            db, tenant_id=ta, authorization_id=prof.authorization_id,
            user_id=ua,
        )
        await db.flush()
        manifest = BrowserExtractionManifest(
            run_id=run.run_id,
            sha256="a" * 64,
            row_count=1,
            expected_columns=["date", "amount"],
            complete=True,
        )
        with pytest.raises(BrowserServiceError):
            await svc.handle_manifest(
                db, agent_job=job, manifest=manifest, agent_tenant_id=ta,
            )
        await db.refresh(run)
        assert run.status == "failed"


@pytest.mark.asyncio
async def test_upload_happy_path(two_tenants):
    ta, ua = two_tenants["tenant_a"], two_tenants["user_a"]
    async with SessionLocal() as db:
        _prof, _agent, job, run = await _setup_ready_to_extract(db, ta, ua)
        content = _csv_bytes([("2026-01-01", "10"), ("2026-01-02", "20")])
        digest = _sha256(content)
        manifest = BrowserExtractionManifest(
            run_id=run.run_id, sha256=digest, row_count=2,
            expected_columns=["date", "amount"], complete=True,
        )
        updated_run = await svc.handle_manifest(
            db, agent_job=job, manifest=manifest, agent_tenant_id=ta,
        )
        await db.commit()

        ingestion_job = await svc.accept_upload(
            db,
            run=updated_run,
            agent_tenant_id=ta,
            content=content,
            content_hash=digest,
        )
        await db.commit()

        assert ingestion_job.status == "pending"
        assert ingestion_job.pending_path == f"browser-{run.run_id}.csv"
        assert ingestion_job.pending_hash == digest
        await db.refresh(updated_run)
        assert updated_run.status == "validating"
        assert updated_run.actual_sha256 == digest
        assert updated_run.actual_row_count == 2
        assert updated_run.completeness_report["columns_extra"] == []
        assert updated_run.completeness_report["columns_missing"] == []


@pytest.mark.asyncio
async def test_upload_rejects_hash_mismatch(two_tenants):
    ta, ua = two_tenants["tenant_a"], two_tenants["user_a"]
    async with SessionLocal() as db:
        _prof, _agent, job, run = await _setup_ready_to_extract(db, ta, ua)
        content = _csv_bytes([("2026-01-01", "10")])
        digest = _sha256(content)
        manifest = BrowserExtractionManifest(
            run_id=run.run_id, sha256=digest, row_count=1,
            expected_columns=["date", "amount"], complete=True,
        )
        updated_run = await svc.handle_manifest(
            db, agent_job=job, manifest=manifest, agent_tenant_id=ta,
        )
        await db.commit()

        with pytest.raises(BrowserServiceError):
            await svc.accept_upload(
                db,
                run=updated_run,
                agent_tenant_id=ta,
                content=b"not a csv",  # different bytes
                content_hash=digest,
            )


@pytest.mark.asyncio
async def test_upload_rejects_missing_columns(two_tenants):
    ta, ua = two_tenants["tenant_a"], two_tenants["user_a"]
    async with SessionLocal() as db:
        _prof, _agent, job, run = await _setup_ready_to_extract(db, ta, ua)
        # Missing 'amount' column.
        content = b"date\n2026-01-01\n"
        digest = _sha256(content)
        manifest = BrowserExtractionManifest(
            run_id=run.run_id, sha256=digest, row_count=1,
            expected_columns=["date", "amount"], complete=True,
        )
        updated_run = await svc.handle_manifest(
            db, agent_job=job, manifest=manifest, agent_tenant_id=ta,
        )
        await db.commit()

        with pytest.raises(BrowserServiceError):
            await svc.accept_upload(
                db,
                run=updated_run,
                agent_tenant_id=ta,
                content=content,
                content_hash=digest,
            )


@pytest.mark.asyncio
async def test_upload_extra_columns_recorded(two_tenants):
    ta, ua = two_tenants["tenant_a"], two_tenants["user_a"]
    async with SessionLocal() as db:
        _prof, _agent, job, run = await _setup_ready_to_extract(db, ta, ua)
        content = _csv_bytes(
            [("2026-01-01", "10")],
            headers=("date", "amount", "extra_col"),
        )
        digest = _sha256(content)
        manifest = BrowserExtractionManifest(
            run_id=run.run_id, sha256=digest, row_count=1,
            expected_columns=["date", "amount"], complete=True,
        )
        updated_run = await svc.handle_manifest(
            db, agent_job=job, manifest=manifest, agent_tenant_id=ta,
        )
        await db.commit()

        await svc.accept_upload(
            db,
            run=updated_run,
            agent_tenant_id=ta,
            content=content,
            content_hash=digest,
        )
        await db.commit()
        await db.refresh(updated_run)
        assert updated_run.completeness_report["columns_extra"] == ["extra_col"]