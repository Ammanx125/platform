# app/services/browser/service.py
"""
Browser extraction service layer.

Stage 2 scope: authorization lifecycle only. Profiles and runs arrive in
later stages and will add their own functions to this module.

A BrowserAuthorization is the platform's record that the tenant approved
browser-based extraction for a specific source and origin. The platform
refuses to issue a browser_extract job when no active authorization
exists. That refusal lives in the job-issuing code (stage 4); stage 2
only manages the records.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.browser import BrowserAuthorization, BrowserExtractionRun
from app.db.models.dataset import DataSource
from app.services.audit import service as audit_service
from app.services.audit import types as audit_types

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.agent import Agent, AgentJob
from app.db.models.browser import (
    IN_FLIGHT_RUN_STATES,
    TERMINAL_RUN_STATES,
    BrowserAuthorization,
    BrowserExtractionProfile,
    BrowserExtractionRun,
)
from app.db.models.dataset import DataSource
from app.schemas.browser import (
    BrowserExtractionJobParams,
    BrowserExtractionPaginationSpec,
    BrowserExtractionProfileCreate,
    BrowserExtractionReportSpec,
)


class BrowserServiceError(Exception):
    """Raised for business-level failures the router maps to 4xx."""


async def _load_agent_source(
    db: AsyncSession, *, tenant_id: uuid.UUID, source_id: uuid.UUID
) -> DataSource:
    """
    Resolve a source and assert it is an agent source owned by the tenant.

    Browser extraction is scoped to on-prem agents. We do NOT allow it on
    csv/excel/sql/http/webhook sources even if a caller somehow constructs
    a request for them.
    """
    source = (
        await db.execute(
            select(DataSource).where(
                DataSource.id == source_id,
                DataSource.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if source is None:
        raise BrowserServiceError("source not found for tenant")
    if source.source_type != "agent":
        raise BrowserServiceError(
            f"browser extraction requires an 'agent' source, "
            f"got {source.source_type!r}"
        )
    return source


async def create_authorization(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    user_id: uuid.UUID,
    origin: str,
    approved_purpose: str,
    scope: dict,
    evidence_ref: str | None,
) -> BrowserAuthorization:
    """
    Record that the tenant has authorized browser extraction for a given
    source and origin.

    Idempotency: if an active authorization already exists for
    (tenant, source, origin), we return it unchanged. This makes the
    endpoint safe to retry without creating a duplicate active record.
    """
    source = await _load_agent_source(
        db, tenant_id=tenant_id, source_id=source_id
    )

    existing = (
        await db.execute(
            select(BrowserAuthorization).where(
                BrowserAuthorization.tenant_id == tenant_id,
                BrowserAuthorization.source_id == source.id,
                BrowserAuthorization.origin == origin,
                BrowserAuthorization.status == "active",
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    auth = BrowserAuthorization(
        tenant_id=tenant_id,
        source_id=source.id,
        authorized_by_user_id=user_id,
        origin=origin,
        approved_purpose=approved_purpose,
        scope=dict(scope or {}),
        evidence_ref=evidence_ref,
        status="active",
    )
    db.add(auth)
    await db.flush()

    await audit_service.emit(
        db,
        tenant_id=tenant_id,
        event_type=audit_types.BROWSER_AUTHORIZATION_CREATED,
        actor_user_id=user_id,
        subject_type="browser_authorization",
        subject_id=auth.id,
        metadata={
            "source_id": str(source.id),
            "origin": origin,
            "evidence_ref": evidence_ref,
        },
        message=f"browser authorization created for {origin}",
    )
    return auth


async def revoke_authorization(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    authorization_id: uuid.UUID,
    user_id: uuid.UUID,
) -> BrowserAuthorization:
    """
    Revoke an authorization. Rows are never deleted — they carry the audit
    trail of who approved access and when it was withdrawn.

    Idempotent: revoking an already-revoked authorization returns the
    existing row unchanged.
    """
    auth = (
        await db.execute(
            select(BrowserAuthorization)
            .where(
                BrowserAuthorization.id == authorization_id,
                BrowserAuthorization.tenant_id == tenant_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if auth is None:
        raise BrowserServiceError("authorization not found")

    if auth.status == "revoked":
        return auth

    auth.status = "revoked"
    auth.revoked_at = datetime.now(UTC)
    auth.revoked_by_user_id = user_id
    await db.flush()

    await audit_service.emit(
        db,
        tenant_id=tenant_id,
        event_type=audit_types.BROWSER_AUTHORIZATION_REVOKED,
        actor_user_id=user_id,
        subject_type="browser_authorization",
        subject_id=auth.id,
        metadata={
            "source_id": str(auth.source_id),
            "origin": auth.origin,
        },
        message=f"browser authorization revoked for {auth.origin}",
    )
    return auth


async def list_authorizations(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID | None = None,
    include_revoked: bool = False,
) -> list[BrowserAuthorization]:
    stmt = select(BrowserAuthorization).where(
        BrowserAuthorization.tenant_id == tenant_id,
    )
    if source_id is not None:
        stmt = stmt.where(BrowserAuthorization.source_id == source_id)
    if not include_revoked:
        stmt = stmt.where(BrowserAuthorization.status == "active")
    stmt = stmt.order_by(BrowserAuthorization.created_at.desc())
    return list((await db.execute(stmt)).scalars().all())


async def get_active_authorization(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    origin: str,
) -> BrowserAuthorization | None:
    """
    Used by the job-issuing path (stage 4) to gate extraction on an active
    authorization. Kept here so the query has exactly one home.
    """
    return (
        await db.execute(
            select(BrowserAuthorization).where(
                BrowserAuthorization.tenant_id == tenant_id,
                BrowserAuthorization.source_id == source_id,
                BrowserAuthorization.origin == origin,
                BrowserAuthorization.status == "active",
            )
        )
    ).scalar_one_or_none()

# ---------- profiles ----------

_PROFILE_STATUSES = frozenset({"active", "paused", "archived"})


async def _load_profile(
    db: AsyncSession, *, tenant_id: uuid.UUID, profile_id: uuid.UUID
) -> BrowserExtractionProfile:
    prof = (
        await db.execute(
            select(BrowserExtractionProfile).where(
                BrowserExtractionProfile.id == profile_id,
                BrowserExtractionProfile.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if prof is None:
        raise BrowserServiceError("profile not found")
    return prof


async def _load_agent_for_source(
    db: AsyncSession, *, tenant_id: uuid.UUID, source_id: uuid.UUID
) -> Agent:
    agent = (
        await db.execute(
            select(Agent).where(
                Agent.source_id == source_id,
                Agent.tenant_id == tenant_id,
                Agent.status == "active",
            )
        )
    ).scalar_one_or_none()
    if agent is None:
        raise BrowserServiceError(
            "no active agent for this source; enroll and register an agent first"
        )
    return agent


async def create_profile(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: BrowserExtractionProfileCreate,
) -> BrowserExtractionProfile:
    """
    Create an extraction profile.

    Rules enforced here, in this order:
      1. Source must be agent-type and tenant-owned.
      2. Authorization must exist, be tenant-owned, and reference the
         same source.
      3. Authorization must be active.
      4. No other profile may exist for this source (v1: one per source).
      5. No other profile with the same name in this tenant.

    Sets DataSource.snapshot_mode='replace_active' as a side effect so the
    scope helper knows to apply the active-snapshot rule immediately.
    """
    source = await _load_agent_source(
        db, tenant_id=tenant_id, source_id=source_id
    )

    auth = (
        await db.execute(
            select(BrowserAuthorization).where(
                BrowserAuthorization.id == payload.authorization_id,
                BrowserAuthorization.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if auth is None:
        raise BrowserServiceError("authorization not found")
    if auth.source_id != source.id:
        raise BrowserServiceError(
            "authorization does not belong to this source"
        )
    if auth.status != "active":
        raise BrowserServiceError(
            f"authorization is {auth.status!r}, not active"
        )

    existing_for_source = (
        await db.execute(
            select(BrowserExtractionProfile).where(
                BrowserExtractionProfile.tenant_id == tenant_id,
                BrowserExtractionProfile.source_id == source.id,
            )
        )
    ).scalar_one_or_none()
    if existing_for_source is not None:
        raise BrowserServiceError(
            "this source already has an extraction profile; "
            "v1 supports one profile per source"
        )

    existing_with_name = (
        await db.execute(
            select(BrowserExtractionProfile).where(
                BrowserExtractionProfile.tenant_id == tenant_id,
                BrowserExtractionProfile.name == payload.name,
            )
        )
    ).scalar_one_or_none()
    if existing_with_name is not None:
        raise BrowserServiceError(
            f"a profile named {payload.name!r} already exists"
        )

    prof = BrowserExtractionProfile(
        tenant_id=tenant_id,
        source_id=source.id,
        authorization_id=auth.id,
        name=payload.name,
        report_identifier=payload.report_identifier,
        revision=1,
        extraction_instructions=dict(payload.extraction_instructions or {}),
        expected_columns=list(payload.expected_columns),
        pagination=payload.pagination,
        limits=dict(payload.limits or {}),
        snapshot_mode="replace_active",
        status="active",
    )
    db.add(prof)
    await db.flush()

    if source.snapshot_mode != "replace_active":
        source.snapshot_mode = "replace_active"
        await db.flush()

    await audit_service.emit(
        db,
        tenant_id=tenant_id,
        event_type=audit_types.BROWSER_PROFILE_CREATED,
        actor_user_id=user_id,
        subject_type="browser_extraction_profile",
        subject_id=prof.id,
        metadata={
            "source_id": str(source.id),
            "authorization_id": str(auth.id),
            "report_identifier": prof.report_identifier,
        },
        message=f"browser profile {prof.name!r} created",
    )
    return prof


async def list_profiles(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID | None = None,
    include_archived: bool = False,
) -> list[BrowserExtractionProfile]:
    stmt = select(BrowserExtractionProfile).where(
        BrowserExtractionProfile.tenant_id == tenant_id,
    )
    if source_id is not None:
        stmt = stmt.where(BrowserExtractionProfile.source_id == source_id)
    if not include_archived:
        stmt = stmt.where(BrowserExtractionProfile.status != "archived")
    stmt = stmt.order_by(BrowserExtractionProfile.created_at.desc())
    return list((await db.execute(stmt)).scalars().all())


async def set_profile_status(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    profile_id: uuid.UUID,
    user_id: uuid.UUID,
    status: str,
) -> BrowserExtractionProfile:
    if status not in _PROFILE_STATUSES:
        raise BrowserServiceError(f"invalid status: {status!r}")
    prof = await _load_profile(
        db, tenant_id=tenant_id, profile_id=profile_id
    )
    if prof.status == status:
        return prof
    previous = prof.status
    prof.status = status
    await db.flush()

    await audit_service.emit(
        db,
        tenant_id=tenant_id,
        event_type=audit_types.BROWSER_PROFILE_STATUS_CHANGED,
        actor_user_id=user_id,
        subject_type="browser_extraction_profile",
        subject_id=prof.id,
        metadata={"from": previous, "to": status},
        message=f"browser profile {prof.name!r} -> {status}",
    )
    return prof


# ---------- scheduling ----------

async def schedule_extraction(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    profile_id: uuid.UUID,
    user_id: uuid.UUID,
) -> tuple[AgentJob, BrowserExtractionRun]:
    """
    Create an AgentJob(browser_extract) and its BrowserExtractionRun.

    All identity is server-owned: run_id is a fresh uuid4 string,
    generated here, never reused. The agent echoes it in its manifest.
    Mismatches are rejected by the manifest handler (stage 5).

    Transactional scheduling:
      - SELECT ... FOR UPDATE on the profile row.
      - Authorization re-checked inside the lock.
      - Partial unique index uq_browser_runs_one_in_flight_per_profile
        guarantees one-run-per-profile under concurrent requests.
      - IntegrityError on that index is mapped to a conflict error.

    Gate order (all must pass):
      1. Profile exists, is tenant-owned, status='active'.
      2. Authorization is active.
      3. Source is agent-type and has an active agent.
      4. No other run for this profile is currently in flight.
    """
    prof = (
        await db.execute(
            select(BrowserExtractionProfile)
            .where(
                BrowserExtractionProfile.id == profile_id,
                BrowserExtractionProfile.tenant_id == tenant_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if prof is None:
        raise BrowserServiceError("profile not found")
    if prof.status != "active":
        raise BrowserServiceError(
            f"profile status is {prof.status!r}; only active profiles "
            "may be scheduled"
        )

    auth = (
        await db.execute(
            select(BrowserAuthorization).where(
                BrowserAuthorization.id == prof.authorization_id,
                BrowserAuthorization.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if auth is None:
        raise BrowserServiceError("profile's authorization no longer exists")
    if auth.status != "active":
        raise BrowserServiceError(
            f"authorization is {auth.status!r}; extraction refused"
        )

    agent = await _load_agent_for_source(
        db, tenant_id=tenant_id, source_id=prof.source_id
    )

    # Friendly early check; the partial index is the hard guarantee.
    in_flight = (
        await db.execute(
            select(BrowserExtractionRun.id).where(
                BrowserExtractionRun.tenant_id == tenant_id,
                BrowserExtractionRun.profile_id == prof.id,
                BrowserExtractionRun.status.in_(IN_FLIGHT_RUN_STATES),
            ).limit(1)
        )
    ).scalar_one_or_none()
    if in_flight is not None:
        raise BrowserServiceError(
            "this profile already has an extraction in flight; "
            "wait for it to finish or fail before scheduling another"
        )

    run_id = str(uuid.uuid4())

    instructions = prof.extraction_instructions or {}
    report_spec = BrowserExtractionReportSpec(
        start_url_path=instructions.get("start_url_path", "/"),
        report_nav=list(instructions.get("report_nav", [])),
        date_range=instructions.get("date_range"),
        column_selectors=dict(instructions.get("column_selectors", {})),
    )
    pagination = prof.pagination or {}
    pagination_spec = BrowserExtractionPaginationSpec(
        method=pagination.get("method", "next_button"),
        max_pages=int(pagination.get("max_pages", 1)),
        next_selector=pagination.get("next_selector"),
    )
    limits = prof.limits or {}
    params_model = BrowserExtractionJobParams(
        schema_version=1,
        run_id=run_id,
        profile_id=prof.id,
        profile_revision=prof.revision,
        source_id=prof.source_id,
        authorization_id=auth.id,
        approved_origin=auth.origin,
        report_spec=report_spec,
        pagination_spec=pagination_spec,
        expected_columns=list(prof.expected_columns),
        max_rows=int(limits.get("max_rows", settings.browser_max_rows_default)),
        max_pages=int(limits.get("max_pages", pagination_spec.max_pages)),
        timeout_seconds=int(
            limits.get("timeout_seconds", settings.browser_run_timeout_seconds)
        ),
        output_format="csv",
    )

    agent_job = AgentJob(
        tenant_id=tenant_id,
        agent_id=agent.id,
        job_type="browser_extract",
        params=params_model.model_dump(mode="json"),
        status="pending",
    )
    db.add(agent_job)
    await db.flush()

    run = BrowserExtractionRun(
        tenant_id=tenant_id,
        profile_id=prof.id,
        agent_job_id=agent_job.id,
        run_id=run_id,
        status="scheduled",
    )
    db.add(run)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise BrowserServiceError(
            "another extraction for this profile is already in flight"
        ) from exc

    await audit_service.emit(
        db,
        tenant_id=tenant_id,
        event_type=audit_types.BROWSER_RUN_SCHEDULED,
        actor_user_id=user_id,
        subject_type="browser_extraction_run",
        subject_id=run.id,
        metadata={
            "profile_id": str(prof.id),
            "agent_job_id": str(agent_job.id),
            "origin": auth.origin,
        },
        message=f"browser extraction scheduled for profile {prof.name!r}",
    )
    return agent_job, run


async def fail_run(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    error: str,
) -> BrowserExtractionRun:
    """
    Mark a run failed. Idempotent. Releases the in-flight slot.
    """
    run = (
        await db.execute(
            select(BrowserExtractionRun)
            .where(
                BrowserExtractionRun.id == run_id,
                BrowserExtractionRun.tenant_id == tenant_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if run is None:
        raise BrowserServiceError("run not found")
    if run.status in TERMINAL_RUN_STATES:
        return run
    run.status = "failed"
    run.error = error[:2000]
    await db.flush()

    await audit_service.emit(
        db,
        tenant_id=tenant_id,
        event_type=audit_types.BROWSER_RUN_FAILED,
        subject_type="browser_extraction_run",
        subject_id=run.id,
        metadata={"error": error[:500]},
        message="browser extraction run failed",
    )
    return run


async def reap_stale_runs(
    db: AsyncSession, *, tenant_id: uuid.UUID | None = None
) -> int:
    """
    Fail runs stuck in in-flight states past their deadline.

    Deadline: created_at + settings.browser_run_timeout_seconds.
    Called periodically by a worker task. Returns count reaped.

    Only reaps states where the server has handed control to the agent:
    extracting, manifest_received, awaiting_upload. States 'validating'
    and 'activating' are actively worked on by request handlers and are
    short-lived; the reaper does not race them.

    Does NOT advance the profile's active_ingestion_job_id. A failed run
    simply releases the in-flight slot; the previous active snapshot, if
    any, remains active.
    """
    reapable = ("extracting", "manifest_received", "awaiting_upload")
    cutoff = datetime.now(UTC) - timedelta(
        seconds=settings.browser_run_timeout_seconds
    )

    stmt = select(BrowserExtractionRun).where(
        BrowserExtractionRun.status.in_(reapable),
        BrowserExtractionRun.created_at < cutoff,
    )
    if tenant_id is not None:
        stmt = stmt.where(BrowserExtractionRun.tenant_id == tenant_id)
    rows = list((await db.execute(stmt)).scalars().all())

    for run in rows:
        run.status = "failed"
        run.error = "run timed out"
    if rows:
        await db.flush()

    return len(rows)


async def list_runs(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    profile_id: uuid.UUID | None = None,
    limit: int = 50,
) -> list[BrowserExtractionRun]:
    stmt = select(BrowserExtractionRun).where(
        BrowserExtractionRun.tenant_id == tenant_id,
    )
    if profile_id is not None:
        stmt = stmt.where(BrowserExtractionRun.profile_id == profile_id)
    stmt = stmt.order_by(BrowserExtractionRun.created_at.desc()).limit(limit)
    return list((await db.execute(stmt)).scalars().all())


async def get_run(
    db: AsyncSession, *, tenant_id: uuid.UUID, run_id: uuid.UUID
) -> BrowserExtractionRun | None:
    return (
        await db.execute(
            select(BrowserExtractionRun).where(
                BrowserExtractionRun.id == run_id,
                BrowserExtractionRun.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()