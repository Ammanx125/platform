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

import csv
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
from app.db.models.dataset import DataSource, IngestionJob
from app.schemas.browser import (
    BrowserExtractionJobParams,
    BrowserExtractionManifest,
    BrowserExtractionPaginationSpec,
    BrowserExtractionProfileCreate,
    BrowserExtractionReportSpec,
)
from app.services.audit import service as audit_service
from app.services.audit import types as audit_types


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


async def _assert_run_still_authorized(
    db: AsyncSession, *, run: BrowserExtractionRun
) -> None:
    """
    Raise if the run's source/origin authorization is no longer active.

    Called at upload acceptance and again before activation so a revocation
    cannot be outrun by an extraction already in flight.
    """
    profile = (
        await db.execute(
            select(BrowserExtractionProfile).where(
                BrowserExtractionProfile.id == run.profile_id,
                BrowserExtractionProfile.tenant_id == run.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if profile is None:
        raise BrowserServiceError(
            "the extraction profile for this run no longer exists"
        )

    authorization = (
        await db.execute(
            select(BrowserAuthorization).where(
                BrowserAuthorization.id == profile.authorization_id,
                BrowserAuthorization.tenant_id == run.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if authorization is None or authorization.status != "active":
        raise BrowserServiceError(
            "browser extraction authorization for this run is no longer active"
        )

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

# ---------- manifest + upload (stage 5) ----------
async def handle_manifest(
    db: AsyncSession,
    *,
    agent_job: AgentJob,
    manifest: BrowserExtractionManifest,
    agent_tenant_id: uuid.UUID,
) -> BrowserExtractionRun:
    """
    Process a manifest reported by the agent for a browser_extract job.

    Validates that:
      - the job is a browser_extract job, in_progress
      - the job belongs to the given tenant
      - the job's params still parse as a valid BrowserExtractionJobParams
      - the manifest's run_id matches the run scheduled for this job
      - the run is in a state that permits accepting a manifest
        ('scheduled' or 'extracting')
      - the run's profile still exists and its authorization is active

    On success, transitions the run to 'manifest_received' and stores
    the manifest's claimed values (expected_sha256, expected_row_count).
    Returns the run. The caller's API handler will respond to the agent
    with an "awaiting_upload" directive; the agent will then POST the
    bytes to the existing upload endpoint.

    Idempotent: if the same manifest arrives twice for the same run and
    the same sha256, the second call is a no-op. If a *different* sha256
    arrives for the same run, that's a conflict.
    """
    if agent_job.job_type != "browser_extract":
        raise BrowserServiceError(
            f"manifest handler called for job_type={agent_job.job_type!r}"
        )
    if agent_job.tenant_id != agent_tenant_id:
        raise BrowserServiceError("job does not belong to this tenant")

    # Re-validate params; a stored job's params could predate a schema
    # change, so we never trust them without re-validating.
    try:
        params = BrowserExtractionJobParams.model_validate(agent_job.params)
    except Exception as exc:  # noqa: BLE001
        raise BrowserServiceError(
            f"job params are not a valid browser_extract payload: {exc}"
        ) from exc

    if params.run_id != manifest.run_id:
        raise BrowserServiceError(
            "manifest run_id does not match the scheduled run"
        )

    run = (
        await db.execute(
            select(BrowserExtractionRun)
            .where(
                BrowserExtractionRun.agent_job_id == agent_job.id,
                BrowserExtractionRun.tenant_id == agent_tenant_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if run is None:
        raise BrowserServiceError("no run scheduled for this agent job")

    # Idempotency: same manifest, same sha256 → no-op.
    if (
        run.status == "manifest_received"
        and run.expected_sha256 == manifest.sha256
        and run.expected_row_count == manifest.row_count
    ):
        return run
    # Conflicting sha256 on the same run → conflict.
    if (
        run.status == "manifest_received"
        and run.expected_sha256 != manifest.sha256
    ):
        raise BrowserServiceError(
            "manifest replayed with a different sha256"
        )

    if run.status not in ("scheduled", "extracting"):
        raise BrowserServiceError(
            f"run is {run.status!r}; cannot accept a manifest"
        )

    # Re-check the profile + authorization — the agent may have run
    # for 10 minutes and the tenant may have revoked access mid-flight.
    profile = (
        await db.execute(
            select(BrowserExtractionProfile).where(
                BrowserExtractionProfile.id == run.profile_id,
                BrowserExtractionProfile.tenant_id == agent_tenant_id,
            )
        )
    ).scalar_one_or_none()
    if profile is None:
        raise BrowserServiceError("run references missing profile")

    auth = (
        await db.execute(
            select(BrowserAuthorization).where(
                BrowserAuthorization.id == profile.authorization_id,
                BrowserAuthorization.tenant_id == agent_tenant_id,
            )
        )
    ).scalar_one_or_none()
    if auth is None or auth.status != "active":
        await fail_run(
            db, tenant_id=agent_tenant_id, run_id=run.id,
            error="authorization revoked before manifest receipt",
        )
        raise BrowserServiceError(
            "authorization was revoked while extraction was in progress"
        )

    run.status = "manifest_received"
    run.expected_sha256 = manifest.sha256
    run.expected_row_count = manifest.row_count
    await db.flush()
    return run


async def accept_upload(
    db: AsyncSession,
    *,
    run: BrowserExtractionRun,
    agent_tenant_id: uuid.UUID,
    content: bytes,
    content_hash: str,
) -> IngestionJob:
    """
    Accept the uploaded CSV for a browser run.

    Verifies:
      - the run belongs to the tenant
      - the run is in 'awaiting_upload' (set by the API handler after
        handle_manifest), or 'manifest_received' if the agent uploaded
        in one round-trip without waiting
      - the received bytes hash to the expected sha256
      - the CSV parses (proper CSV with header row)
      - the header set matches the profile's expected_columns (a
        missing column is a hard fail; extra columns are recorded in
        the completeness report)
      - the row count is within tolerance of the manifest's claim

    On success:
      - records a FileObservation
      - creates a pending IngestionJob with pending_path ending in '.csv'
      - stores bytes under build_storage_key
      - sets run.status='validating', run.ingestion_job_id=<new job>
      - returns the ingestion job; the caller commits

    The activation step happens later, from run_job()'s success branch,
    once the ingestion worker finishes. This function never activates.

    Errors leave the run untouched so the caller can decide to fail it
    or let the reaper time it out.
    """
    import hashlib
    from io import StringIO

    from app.services.ingestion import service as ingestion_service
    from app.services.storage.local import storage
    from app.services.timestamps.files import observe_file

    if run.tenant_id != agent_tenant_id:
        raise BrowserServiceError("run does not belong to this tenant")
    if run.status not in ("manifest_received", "awaiting_upload"):
        raise BrowserServiceError(
            f"run is {run.status!r}; cannot accept an upload"
        )

    # Authorization is rechecked here, not only when the manifest arrived.
    # Revocation between the manifest and the upload must stop the data
    # landing, otherwise "revoke" only takes effect for extractions that
    # have not started yet.
    await _assert_run_still_authorized(db, run=run)

    actual_hash = hashlib.sha256(content).hexdigest()
    if actual_hash != content_hash:
        raise BrowserServiceError(
            f"content hash mismatch: expected {content_hash}, got {actual_hash}"
        )
    if run.expected_sha256 is not None and actual_hash != run.expected_sha256:
        raise BrowserServiceError(
            "uploaded bytes do not match the manifest's sha256"
        )

    # Parse the CSV.
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise BrowserServiceError(
            f"uploaded CSV is not valid UTF-8: {exc}"
        ) from exc
    reader = csv.reader(StringIO(text))
    try:
        header = next(reader)
    except StopIteration as exc:
        raise BrowserServiceError("uploaded CSV is empty") from exc
    header = [h.strip() for h in header]
    if any(not h for h in header):
        raise BrowserServiceError("uploaded CSV has empty column names")

    profile = (
        await db.execute(
            select(BrowserExtractionProfile).where(
                BrowserExtractionProfile.id == run.profile_id,
                BrowserExtractionProfile.tenant_id == agent_tenant_id,
            )
        )
    ).scalar_one_or_none()
    if profile is None:
        raise BrowserServiceError("run references missing profile")

    expected = list(profile.expected_columns)
    missing = [c for c in expected if c not in header]
    if missing:
        raise BrowserServiceError(
            f"uploaded CSV is missing required columns: {missing}"
        )
    extra = [c for c in header if c not in expected]

    row_count = sum(1 for _ in reader)

    tolerance_pct = float((profile.limits or {}).get("row_tolerance_pct", 2.0))
    expected_rows = run.expected_row_count
    within_tolerance = True
    tolerance_note: str | None = None
    if expected_rows is not None and expected_rows > 0:
        diff_pct = abs(row_count - expected_rows) / expected_rows * 100.0
        within_tolerance = diff_pct <= tolerance_pct
        if not within_tolerance:
            tolerance_note = (
                f"row count {row_count} differs from manifest claim "
                f"{expected_rows} by {diff_pct:.2f}% (tolerance {tolerance_pct}%)"
            )

    completeness_report = {
        "hash_matches": True,
        "row_count_matches": within_tolerance,
        "columns_present": [c for c in expected if c in header],
        "columns_missing": missing,
        "columns_extra": extra,
        "row_count_within_tolerance": within_tolerance,
        "tolerance_pct": tolerance_pct,
        "manifest_row_count": expected_rows,
        "parsed_row_count": row_count,
        "notes": tolerance_note,
    }

    if not within_tolerance:
        raise BrowserServiceError(
            tolerance_note or "row count outside tolerance"
        )

    # Record the observation and stage the file.
    storage_key = ingestion_service.build_storage_key(
        tenant_id=agent_tenant_id,
        source_id=profile.source_id,
        extension=".csv",
    )
    await storage.put(key=storage_key, content=content)

    pending_path = f"browser-{run.run_id}.csv"
    await observe_file(
        db,
        tenant_id=agent_tenant_id,
        source_id=profile.source_id,
        path=pending_path,
        content_hash=actual_hash,
        byte_size=len(content),
        storage_key=storage_key,
    )

    job = IngestionJob(
        tenant_id=agent_tenant_id,
        source_id=profile.source_id,
        status="pending",
        storage_key=storage_key,
        pending_path=pending_path,
        pending_hash=actual_hash,
    )
    db.add(job)
    await db.flush()

    run.status = "validating"
    run.actual_sha256 = actual_hash
    run.actual_row_count = row_count
    run.ingestion_job_id = job.id
    run.completeness_report = completeness_report
    await db.flush()

    await audit_service.emit(
        db,
        tenant_id=agent_tenant_id,
        event_type=audit_types.BROWSER_RUN_UPLOAD_RECEIVED,
        subject_type="browser_extraction_run",
        subject_id=run.id,
        metadata={
            "ingestion_job_id": str(job.id),
            "actual_sha256": actual_hash,
            "actual_row_count": row_count,
            "columns_extra": extra,
        },
        message=f"browser snapshot received for run {run.run_id}",
    )
    return job