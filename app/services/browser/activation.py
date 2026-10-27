# app/services/browser/activation.py
"""
Snapshot activation.

Called at two points in the lifecycle:

  1. From run_job() in app/services/ingestion/service.py, immediately
     after a CSV ingestion job succeeds. If the job was created by a
     browser extraction run, this helper performs the pointer swap.

  2. From the upload handler, after it has created the IngestionJob
     (which arrives here only on the next ingestion-worker cycle).

The transaction boundary is the ingestion-success commit. Activation is
atomic with it: either both happen or neither does. This is the
invariant the design demands — a failed ingestion never replaces good
data, and a successful ingestion always becomes the active snapshot.

Concurrency: the profile row is locked FOR UPDATE during the swap. Two
concurrent activations for the same profile cannot race; the second
waits, sees the first's result, and either proceeds or no-ops.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.browser import (
    TERMINAL_RUN_STATES,
    BrowserExtractionProfile,
    BrowserExtractionRun,
)
from app.db.models.dataset import IngestionJob
from app.services.audit import service as audit_service
from app.services.audit import types as audit_types


class ActivationError(Exception):
    """The run cannot be activated for a structural reason."""


async def maybe_activate_snapshot(
    db: AsyncSession, *, ingestion_job: IngestionJob
) -> BrowserExtractionRun | None:
    """
    If this ingestion job was created by a browser extraction run and the
    run is in 'validating' or 'activating', activate the snapshot.

    Returns the activated run, or None if this ingestion job wasn't a
    browser run's, or the run wasn't in an activatable state.

    Raises ActivationError only for structural problems that should fail
    loudly (e.g., the run's profile has been deleted, or the run's
    actual_sha256 is missing despite reaching this stage). A
    non-activatable state (already active, already failed, still
    awaiting_upload) returns None quietly — that's the normal
    "not applicable" case.
    """
    if ingestion_job.status != "succeeded":
        return None

    run = (
        await db.execute(
            select(BrowserExtractionRun)
            .where(
                BrowserExtractionRun.ingestion_job_id == ingestion_job.id,
                BrowserExtractionRun.tenant_id == ingestion_job.tenant_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if run is None:
        return None

    if run.status not in ("validating", "activating"):
        return None
    if run.actual_sha256 is None:
        raise ActivationError(
            f"run {run.id} reached activation with no verified sha256"
        )

    profile = (
        await db.execute(
            select(BrowserExtractionProfile)
            .where(
                BrowserExtractionProfile.id == run.profile_id,
                BrowserExtractionProfile.tenant_id == ingestion_job.tenant_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if profile is None:
        raise ActivationError(
            f"run {run.id} references missing profile {run.profile_id}"
        )

    previous_active_job_id = profile.active_ingestion_job_id

    # If this run is already the active pointer (idempotent re-entry from
    # a retried ingestion), no-op gracefully.
    if previous_active_job_id == ingestion_job.id:
        if run.status == "validating":
            run.status = "active"
            run.activated_at = datetime.now(UTC)
            await db.flush()
        return run

    # Look up the previously active run (if any) to mark it superseded.
    previous_active_run: BrowserExtractionRun | None = None
    if previous_active_job_id is not None:
        previous_active_run = (
            await db.execute(
                select(BrowserExtractionRun).where(
                    BrowserExtractionRun.ingestion_job_id
                    == previous_active_job_id,
                    BrowserExtractionRun.tenant_id == ingestion_job.tenant_id,
                )
            )
        ).scalar_one_or_none()

    # --- swap ---
    profile.active_ingestion_job_id = ingestion_job.id
    run.status = "active"
    run.activated_at = datetime.now(UTC)
    if previous_active_run is not None and previous_active_run.id != run.id:
        previous_active_run.status = "superseded"
        previous_active_run.superseded_at = datetime.now(UTC)
    await db.flush()

    await audit_service.emit(
        db,
        tenant_id=ingestion_job.tenant_id,
        event_type=audit_types.BROWSER_RUN_ACTIVATED,
        subject_type="browser_extraction_run",
        subject_id=run.id,
        metadata={
            "profile_id": str(profile.id),
            "ingestion_job_id": str(ingestion_job.id),
            "previous_active_run_id": (
                str(previous_active_run.id)
                if previous_active_run is not None else None
            ),
            "previous_active_job_id": (
                str(previous_active_job_id)
                if previous_active_job_id is not None else None
            ),
        },
        message=f"browser snapshot activated for profile {profile.name!r}",
    )
    return run


async def maybe_fail_browser_run_on_ingestion_failure(
    db: AsyncSession, *, ingestion_job: IngestionJob
) -> BrowserExtractionRun | None:
    """
    If this ingestion job was created by a browser run and the ingestion
    failed, mark the run failed too. Previous active snapshot remains
    active (we never touch the profile's pointer on failure).
    """
    if ingestion_job.status != "failed":
        return None

    run = (
        await db.execute(
            select(BrowserExtractionRun).where(
                BrowserExtractionRun.ingestion_job_id == ingestion_job.id,
                BrowserExtractionRun.tenant_id == ingestion_job.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if run is None:
        return None
    if run.status in TERMINAL_RUN_STATES:
        return run

    run.status = "failed"
    run.error = (
        f"ingestion failed: {(ingestion_job.error_message or '')[:500]}"
    )
    await db.flush()

    await audit_service.emit(
        db,
        tenant_id=ingestion_job.tenant_id,
        event_type=audit_types.BROWSER_RUN_FAILED_AT_INGESTION,
        subject_type="browser_extraction_run",
        subject_id=run.id,
        metadata={"ingestion_job_id": str(ingestion_job.id)},
        message=f"browser run failed during ingestion: {run.run_id}",
    )
    return run