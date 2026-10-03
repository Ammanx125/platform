# app/services/agents/jobs.py
"""
Agent job lifecycle.

Creating jobs is a service operation called from:
  - the ingest endpoint (creating upload_file jobs for each ready file)
  - the rescan endpoint (creating a rescan job)
  - the future stale-job reaper (retrying jobs whose agent died)

Claiming and completing jobs is called from the agent endpoints.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.agent import Agent, AgentJob
from app.db.models.dataset import IngestionJob
from app.services.agents.classifier import Observation
from app.services.audit import service as audit_service
from app.services.audit import types as audit_types


class JobError(Exception):
    """A job operation failed."""


# Maximum retries before a job is marked failed permanently.
MAX_RETRIES = 3

# After this many seconds, an in_progress job is considered abandoned and
# can be re-claimed. A future worker task enforces this; for now the value
# lives here so the endpoint can decide whether to allow reclaim.
IN_PROGRESS_TIMEOUT_SECONDS = 300


# ---------- creating jobs ----------

async def create_upload_jobs_for_observations(
    db: AsyncSession,
    *,
    agent: Agent,
    job: IngestionJob,
    observations: list[Observation],
) -> list[AgentJob]:
    """
    For each observation, create one AgentJob(type='upload_file') and one
    PendingFileUpload row. Returns the created AgentJobs.
    """
    from app.db.models.agent import PendingFileUpload

    created: list[AgentJob] = []
    for obs in observations:
        agent_job = AgentJob(
            tenant_id=agent.tenant_id,
            agent_id=agent.id,
            job_type="upload_file",
            params={
                "path": obs.path,
                "expected_hash": obs.content_hash,
                "ingestion_job_id": str(job.id),
            },
            status="pending",
        )
        db.add(agent_job)
        await db.flush()

        pending = PendingFileUpload(
            tenant_id=agent.tenant_id,
            job_id=job.id,
            agent_job_id=agent_job.id,
            path=obs.path,
            expected_hash=obs.content_hash,
            status="pending",
        )
        db.add(pending)
        created.append(agent_job)

    await db.flush()
    return created


async def create_rescan_job(
    db: AsyncSession, *, agent: Agent
) -> AgentJob:
    job = AgentJob(
        tenant_id=agent.tenant_id,
        agent_id=agent.id,
        job_type="rescan",
        params={},
        status="pending",
    )
    db.add(job)
    await db.flush()
    return job


# ---------- claiming and completing ----------

async def claim_pending_jobs(
    db: AsyncSession, *, agent: Agent, limit: int = 20
) -> list[AgentJob]:
    """
    Atomically claim up to `limit` pending jobs for an agent.

    Uses a single UPDATE ... WHERE id IN (...) RETURNING to avoid races
    when multiple iterations of the same agent (or a retried request)
    try to claim the same jobs.
    """
    # Find candidate ids without locking, then claim atomically.
    candidate_ids = [
        row[0]
        for row in (
            await db.execute(
                select(AgentJob.id)
                .where(
                    AgentJob.agent_id == agent.id,
                    AgentJob.status == "pending",
                )
                .order_by(AgentJob.created_at.asc())
                .limit(limit)
            )
        ).all()
    ]
    if not candidate_ids:
        return []

    now = datetime.now(UTC)
    stmt = (
        update(AgentJob)
        .where(
            AgentJob.id.in_(candidate_ids),
            AgentJob.status == "pending",
        )
        .values(status="in_progress", claimed_at=now)
        .returning(AgentJob)
    )
    claimed = list((await db.execute(stmt)).scalars().all())
    await db.flush()
    return claimed


async def complete_job(
    db: AsyncSession,
    *,
    job: AgentJob,
    status: str,
    result: dict | None = None,
    error: str | None = None,
) -> AgentJob:
    """
    Report the outcome of a claimed job.

    status: 'completed' | 'rejected' | 'failed'
    """
    if job.status != "in_progress":
        raise JobError(
            f"job is in status {job.status!r}, expected 'in_progress'"
        )
    if status not in ("completed", "rejected", "failed"):
        raise JobError(f"invalid completion status: {status!r}")

    job.status = status
    job.result = result or {}
    job.error = error
    job.finished_at = datetime.now(UTC)
    await db.flush()

    await audit_service.emit(
        db,
        tenant_id=job.tenant_id,
        event_type=audit_types.AGENT_JOB_COMPLETED,
        subject_type="agent_job",
        subject_id=job.id,
        metadata={
            "job_type": job.job_type,
            "status": status,
            "error": error,
        },
        message=f"agent job {job.job_type} {status}",
    )

    # If this was an upload_file job, update the corresponding PendingFileUpload.
    if job.job_type == "upload_file":
        await _update_pending_upload(db, job=job, status=status, error=error)

    return job


async def _update_pending_upload(
    db: AsyncSession, *, job: AgentJob, status: str, error: str | None
) -> None:
    from app.db.models.agent import PendingFileUpload

    pending = (
        await db.execute(
            select(PendingFileUpload).where(
                PendingFileUpload.agent_job_id == job.id,
                PendingFileUpload.tenant_id == job.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if pending is None:
        return

    if status == "completed":
        pending.status = "delivered"
        pending.actual_hash = job.result.get("actual_hash") or pending.expected_hash
    else:
        pending.status = "failed"
        pending.error = (error or "")[:500]

    await db.flush()

    # Check if all pending uploads for this job's ingestion_job are done.
    # If yes, the ingestion can run.
    if status == "completed":
        await _maybe_trigger_ingestion(db, ingestion_job_id=pending.job_id)


async def _maybe_trigger_ingestion(
    db: AsyncSession, *, ingestion_job_id: uuid.UUID
) -> None:
    """
    If every PendingFileUpload for this ingestion job is delivered, run
    the job. Called inline after each successful upload.

    Note: this runs `run_job` synchronously inside the agent's request.
    For v1 that's acceptable — one file at a time, and the file has just
    been written to storage. If ingestion becomes slow, move this to a
    worker task that polls for "ready" jobs.
    """
    from app.db.models.agent import PendingFileUpload
    from app.db.models.dataset import IngestionJob

    job_row = (
        await db.execute(
            select(IngestionJob).where(IngestionJob.id == ingestion_job_id)
        )
    ).scalar_one_or_none()
    if job_row is None:
        return

    tenant_id = job_row.tenant_id

    pending = (
        await db.execute(
            select(PendingFileUpload).where(
                PendingFileUpload.job_id == ingestion_job_id,
                PendingFileUpload.tenant_id == tenant_id,
            )
        )
    ).scalars().all()

    if not pending:
        return

    if any(p.status == "pending" for p in pending):
        # Still waiting on other files.
        return

    delivered = [p for p in pending if p.status == "delivered"]
    if not delivered:
        # Every file failed.
        job_row.status = "failed"
        job_row.error_message = "no files were delivered by the agent"
        job_row.finished_at = datetime.now(UTC)
        await db.flush()
        return

    # Run the job. Import here to avoid circular imports.
    from app.services.ingestion.service import run_job
    await run_job(db, job_id=ingestion_job_id)


async def get_job(
    db: AsyncSession, *, job_id: uuid.UUID, agent_id: uuid.UUID
) -> AgentJob | None:
    return (
        await db.execute(
            select(AgentJob).where(
                AgentJob.id == job_id,
                AgentJob.agent_id == agent_id,
            )
        )
    ).scalar_one_or_none()