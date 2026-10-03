# app/api/v1/agents.py
from __future__ import annotations

import hashlib
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentAgent, CurrentTenantId, require_permission
from app.core.config import settings
from app.db.models.agent import PendingFileUpload
from app.db.models.dataset import IngestionJob
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.agent import (
    AgentConfigRead,
    AgentEnrollRequest,
    AgentEnrollResponse,
    AgentHeartbeatRequest,
    AgentJobRead,
    AgentJobResultRequest,
    AgentJobResultResponse,
    AgentRead,
    AgentRegisterRequest,
    AgentRegisterResponse,
    AgentSyncRequest,
    AgentSyncResponse,
)
from app.services.agents import jobs as agent_jobs_service
from app.services.agents import service as agents_service
from app.services.agents.jobs import JobError
from app.services.agents.service import AgentError
from app.services.audit import service as audit_service
from app.services.audit import types as audit_types
from app.services.ingestion import service as ingestion_service

router = APIRouter(prefix="/agents", tags=["agents"])


# ---------- admin-facing ----------

@router.post(
    "/enroll",
    response_model=AgentEnrollResponse,
    status_code=status.HTTP_201_CREATED,
)
async def enroll_agent(
    body: AgentEnrollRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    user: Annotated[User, Depends(require_permission("dataset:write"))],
) -> AgentEnrollResponse:
    """
    Admin action. Creates an Agent enrollment and returns the one-time
    enrollment token. The token is shown once and never again.
    """
    try:
        agent, token = await agents_service.create_agent_enrollment(
            db,
            tenant_id=tenant_id,
            source_id=body.source_id,
            name=body.name,
            description=body.description,
            enrolled_by_user_id=user.id,
        )
    except AgentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.commit()
    return AgentEnrollResponse(
        agent_id=agent.id,
        enrollment_token=token,
        expires_in_seconds=agents_service.ENROLLMENT_TTL_MINUTES * 60,
    )


@router.get("", response_model=list[AgentRead])
async def list_agents(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("dataset:read"))],
) -> list[AgentRead]:
    agents = await agents_service.list_agents(db, tenant_id=tenant_id)
    return [AgentRead.model_validate(a) for a in agents]


@router.get("/{agent_id}", response_model=AgentRead)
async def get_agent(
    agent_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("dataset:read"))],
) -> AgentRead:
    agent = await agents_service.get_agent(
        db, agent_id=agent_id, tenant_id=tenant_id
    )
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    return AgentRead.model_validate(agent)


# ---------- agent-facing ----------

@router.post(
    "/register",
    response_model=AgentRegisterResponse,
)
async def register_agent(
    body: AgentRegisterRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AgentRegisterResponse:
    """
    Agent exchanges its one-time enrollment token for a long-lived bearer
    credential. Unauthenticated (the enrollment token IS the auth).
    """
    try:
        agent, credential = await agents_service.register_agent(
            db,
            enrollment_token=body.enrollment_token,
            agent_metadata=body.agent_metadata,
        )
    except AgentError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    await db.commit()
    return AgentRegisterResponse(
        agent_id=agent.id,
        credential=credential,
        source_id=agent.source_id,
    )


@router.post("/{agent_id}/heartbeat", response_model=AgentRead)
async def agent_heartbeat(
    agent_id: uuid.UUID,
    body: AgentHeartbeatRequest,
    agent: CurrentAgent,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AgentRead:
    if agent.id != agent_id:
        raise HTTPException(status_code=403, detail="agent mismatch")
    agent = await agents_service.heartbeat(
        db, agent=agent, agent_metadata=body.agent_metadata
    )
    await db.commit()
    await db.refresh(agent)
    return AgentRead.model_validate(agent)


@router.post("/{agent_id}/sync", response_model=AgentSyncResponse)
async def agent_sync(
    agent_id: uuid.UUID,
    body: AgentSyncRequest,
    agent: CurrentAgent,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AgentSyncResponse:
    if agent.id != agent_id:
        raise HTTPException(status_code=403, detail="agent mismatch")
    counts = await agents_service.process_sync_batch(
        db,
        agent=agent,
        files=[f.model_dump() for f in body.files],
    )
    await db.commit()
    return AgentSyncResponse(counts=counts)



@router.get("/{agent_id}/config", response_model=AgentConfigRead)
async def agent_config(
    agent_id: uuid.UUID,
    agent: CurrentAgent,
) -> AgentConfigRead:
    """
    Advisory configuration. The agent may override any field locally and
    reports its effective values on heartbeat.
    """
    if agent.id != agent_id:
        raise HTTPException(status_code=403, detail="agent mismatch")
    return AgentConfigRead(
        sync_interval_seconds=settings.agent_sync_interval_seconds,
        job_poll_interval_seconds=settings.agent_job_poll_interval_seconds,
        max_upload_bytes=settings.agent_max_upload_bytes,
        job_batch_size=settings.agent_job_batch_size,
    )


@router.get("/{agent_id}/jobs", response_model=list[AgentJobRead])
async def agent_poll_jobs(
    agent_id: uuid.UUID,
    agent: CurrentAgent,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = 20,
) -> list[AgentJobRead]:
    """
    Claim pending jobs for this agent. Jobs claimed here are marked
    in_progress and must be reported via POST /jobs/{id}/result.
    """
    if agent.id != agent_id:
        raise HTTPException(status_code=403, detail="agent mismatch")
    claimed = await agent_jobs_service.claim_pending_jobs(
        db, agent=agent, limit=min(limit, settings.agent_job_batch_size)
    )
    await db.commit()
    return [AgentJobRead.model_validate(j) for j in claimed]


@router.post(
    "/{agent_id}/jobs/{job_id}/result",
    response_model=AgentJobResultResponse,
)
async def agent_job_result(
    agent_id: uuid.UUID,
    job_id: uuid.UUID,
    body: AgentJobResultRequest,
    agent: CurrentAgent,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AgentJobResultResponse:
    if agent.id != agent_id:
        raise HTTPException(status_code=403, detail="agent mismatch")
    job = await agent_jobs_service.get_job(
        db, job_id=job_id, agent_id=agent.id
    )
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    try:
        job = await agent_jobs_service.complete_job(
            db, job=job, status=body.status, result=body.result, error=body.error
        )
    except JobError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.commit()
    return AgentJobResultResponse(id=job.id, status=job.status)

@router.post("/{agent_id}/upload/{content_hash}", status_code=status.HTTP_201_CREATED)
async def agent_upload_content(
    agent_id: uuid.UUID,
    content_hash: str,
    agent: CurrentAgent,
    db: Annotated[AsyncSession, Depends(get_db)],
    file: UploadFile = File(...),  # noqa: B008
) -> dict[str, str]:
    """
    The agent pushes file content for a specific content_hash.

    The agent must have a job for this hash. The upload stores the bytes,
    links them to the source, and triggers ingestion for the job if this
    was the last pending file in the batch.
    """
    from app.services.storage.local import storage
    if agent.id != agent_id:
        raise HTTPException(status_code=403, detail="agent mismatch")

    # Find the pending upload row for this hash.
    pending = (
        await db.execute(
            select(PendingFileUpload).where(
                PendingFileUpload.tenant_id == agent.tenant_id,
                PendingFileUpload.expected_hash == content_hash,
                PendingFileUpload.status == "pending",
            )
        )
    ).scalar_one_or_none()
    if pending is None:
        raise HTTPException(
            status_code=404,
            detail="no pending upload for this content hash",
        )

    content = await file.read()
    if len(content) > settings.agent_max_upload_bytes:
        raise HTTPException(status_code=413, detail="file too large")

    actual_hash = hashlib.sha256(content).hexdigest()

    # Store the content. Use the ingestion_job's storage key so run_job
    # finds it.
    ingestion_job = (
        await db.execute(
            select(IngestionJob).where(IngestionJob.id == pending.job_id)
        )
    ).scalar_one_or_none()
    if ingestion_job is None:
        raise HTTPException(status_code=404, detail="ingestion job not found")

    ext = "." + pending.path.rsplit(".", 1)[-1].lower() if "." in pending.path else ""
    storage_key = ingestion_service.build_storage_key(
        tenant_id=agent.tenant_id,
        source_id=agent.source_id,
        extension=ext,
    )
    await storage.put(key=storage_key, content=content)

    ingestion_job.storage_key = storage_key
    ingestion_job.pending_path = pending.path
    ingestion_job.pending_hash = actual_hash

    # Record the observation with the storage_key set.
    from app.services.timestamps.files import observe_file
    await observe_file(
        db,
        tenant_id=agent.tenant_id,
        source_id=agent.source_id,
        path=pending.path,
        content_hash=actual_hash,
        byte_size=len(content),
        storage_key=storage_key,
    )

    # Mark the pending row as delivered.
    pending.status = "delivered"
    pending.actual_hash = actual_hash
    await db.flush()

    await audit_service.emit(
        db,
        tenant_id=agent.tenant_id,
        event_type=audit_types.AGENT_FILE_DELIVERED,
        subject_type="agent",
        subject_id=agent.id,
        metadata={
            "path": pending.path,
            "content_hash": actual_hash,
            "byte_size": len(content),
        },
        message=f"file delivered: {pending.path}",
    )

    # If all pending uploads for this ingestion job are done, run the job.
    from app.services.agents.jobs import _maybe_trigger_ingestion
    await _maybe_trigger_ingestion(db, ingestion_job_id=ingestion_job.id)
    await db.commit()

    return {"status": "delivered", "content_hash": actual_hash}