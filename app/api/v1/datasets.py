# app/api/v1/datasets.py
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentTenantId, require_permission
from app.core.config import settings
from app.db.models.agent import Agent
from app.db.models.dataset import DataSource, IngestionJob
from app.db.models.understanding import DataProfile
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.agent import AgentIngestResponse, ClassificationRead
from app.schemas.dataset import (
    DataSourceCreate,
    DataSourceRead,
    IngestionJobRead,
    WebhookSourceCreate,
    WebhookSourceCreated,
)
from app.schemas.understanding import DataProfileRead
from app.services.agents import classifier as agent_classifier
from app.services.agents import jobs as agent_jobs_service
from app.services.ingestion import service as ingestion_service
from app.services.ingestion import webhook as webhook_service
from app.services.ingestion.base import IngestionError
from app.services.storage.local import storage
from app.services.understanding import service as understanding_service

router = APIRouter(prefix="/datasets", tags=["datasets"])


@router.get("", response_model=list[DataSourceRead])
async def list_datasets(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("dataset:read"))],
) -> list[DataSource]:
    stmt = (
        select(DataSource)
        .where(DataSource.tenant_id == tenant_id)
        .order_by(DataSource.created_at.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


@router.post("", response_model=DataSourceRead, status_code=status.HTTP_201_CREATED)
async def create_dataset(
    body: DataSourceCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("dataset:write"))],
) -> DataSource:
    source = DataSource(
        tenant_id=tenant_id,
        name=body.name,
        source_type=body.source_type,
        config=body.config,
    )
    db.add(source)
    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=f"could not create dataset: {exc}") from exc
    await db.refresh(source)
    return source


@router.get("/{dataset_id}", response_model=DataSourceRead)
async def get_dataset(
    dataset_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("dataset:read"))],
) -> DataSource:
    source = (
        await db.execute(
            select(DataSource).where(
                DataSource.id == dataset_id,
                DataSource.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    return source


@router.post(
    "/{dataset_id}/upload",
    response_model=IngestionJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_dataset(
    dataset_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    user: Annotated[User, Depends(require_permission("dataset:write"))],
    file: UploadFile = File(...),  # noqa: B008
) -> IngestionJob:
    source = (
        await db.execute(
            select(DataSource).where(
                DataSource.id == dataset_id,
                DataSource.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    if source.source_type == "agent":
        raise HTTPException(
            status_code=400,
            detail=(
                "agent sources cannot receive direct uploads; "
                "use POST /api/v1/datasets/{id}/ingest to request "
                "content from the on-prem agent"
            ),
        )

    if not file.filename:
        raise HTTPException(status_code=400, detail="no filename")

    ext = ingestion_service.extension_of(file.filename)
    if ext not in settings.allowed_upload_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"extension not allowed: {ext}",
        )

    compatible_extensions = {
        "csv": {".csv"},
        "excel": {".xlsx", ".xls"},
        "pdf": {".pdf"},
    }
    if source.source_type not in compatible_extensions:
        raise HTTPException(
            status_code=400,
            detail=(
                f"source type {source.source_type!r} does not support "
                "direct file uploads"
            ),
        )
    if ext not in compatible_extensions[source.source_type]:
        raise HTTPException(
            status_code=400,
            detail=(
                f"source is typed {source.source_type!r} but "
                f"received {ext!r}"
            ),
        )

    try:
        ingestion_service.extension_to_source_type(ext)
    except IngestionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    content = await file.read()
    max_bytes = (
        settings.max_pdf_upload_bytes if ext == ".pdf"
        else settings.max_upload_bytes
    )
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"file too large (max {max_bytes} bytes)",
        )

    storage_key = ingestion_service.build_storage_key(
        tenant_id=tenant_id,
        source_id=source.id,
        extension=ext,
    )
    await storage.put(key=storage_key, content=content)

    # Record a file observation. This is the "memory" of the file even
    # after the ingestion pipeline finishes and possibly deletes the bytes.
    from app.services.ingestion.service import hash_bytes
    from app.services.timestamps.files import observe_file

    await observe_file(
        db,
        tenant_id=tenant_id,
        source_id=source.id,
        path=file.filename or storage_key,
        content_hash=hash_bytes(content),
        byte_size=len(content),
        storage_key=storage_key,
    )

    from app.services.events import store as events_store
    from app.services.events import types as event_types

    await events_store.record_event(
        db,
        tenant_id=tenant_id,
        event_type=event_types.FILE_OBSERVED,
        source_id=source.id,
        user_id=user.id,
        actor_kind="user",
        payload={
            "path": file.filename or storage_key,
            "byte_size": len(content),
        },
        dedup_key=f"file.observed:{storage_key}",
    )

    source.config = {
        **source.config,
        "storage_key": storage_key,
        "original_filename": file.filename,
    }

    job = await ingestion_service.enqueue_job(
        db, tenant_id=tenant_id, source_id=source.id
    )
    await db.commit()
    await db.refresh(job)
    return job


@router.get("/{dataset_id}/jobs", response_model=list[IngestionJobRead])
async def list_jobs(
    dataset_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("dataset:read"))],
) -> list[IngestionJob]:
    # Confirm dataset belongs to tenant
    source = (
        await db.execute(
            select(DataSource).where(
                DataSource.id == dataset_id,
                DataSource.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=404, detail="dataset not found")

    stmt = (
        select(IngestionJob)
        .where(
            IngestionJob.source_id == dataset_id,
            IngestionJob.tenant_id == tenant_id,
        )
        .order_by(IngestionJob.created_at.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


@router.get("/{dataset_id}/jobs/{job_id}", response_model=IngestionJobRead)
async def get_job(
    dataset_id: uuid.UUID,
    job_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("dataset:read"))],
) -> IngestionJob:
    job = (
        await db.execute(
            select(IngestionJob).where(
                IngestionJob.id == job_id,
                IngestionJob.source_id == dataset_id,
                IngestionJob.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job

@router.get(
    "/{dataset_id}/jobs/{job_id}/profile",
    response_model=DataProfileRead,
)
async def get_job_profile(
    dataset_id: uuid.UUID,
    job_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("dataset:read"))],
) -> DataProfile:
    # Confirm job belongs to this tenant AND this dataset.
    job = (
        await db.execute(
            select(IngestionJob).where(
                IngestionJob.id == job_id,
                IngestionJob.source_id == dataset_id,
                IngestionJob.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    profile = (
        await db.execute(
            select(DataProfile).where(
                DataProfile.job_id == job_id,
                DataProfile.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if profile is None:
        raise HTTPException(status_code=404, detail="profile not yet computed")
    return profile

@router.post(
    "/webhook",
    response_model=WebhookSourceCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_webhook_source(
    body: WebhookSourceCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("dataset:write"))],
) -> WebhookSourceCreated:
    """
    Create a webhook data source.

    Returns the token and signing secret ONCE. Neither is retrievable later
    in plaintext. The customer configures their sender with:

      URL:    {base_url}/api/v1/webhooks/{token}
      Header: X-Sansa-Signature: hex(HMAC-SHA256(secret, raw_body))
    """
    source, token, secret = await webhook_service.create_webhook_source(
        db, tenant_id=tenant_id, name=body.name
    )
    await db.commit()
    await db.refresh(source)
    return WebhookSourceCreated(
        id=source.id,
        name=source.name,
        source_type=source.source_type,
        token=token,
        secret=secret,
    )

@router.post(
    "/{dataset_id}/jobs/{job_id}/profile",
    response_model=DataProfileRead,
)
async def recompute_job_profile(
    dataset_id: uuid.UUID,
    job_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("dataset:write"))],
) -> DataProfile:
    job = (
        await db.execute(
            select(IngestionJob).where(
                IngestionJob.id == job_id,
                IngestionJob.source_id == dataset_id,
                IngestionJob.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    profile = await understanding_service.compute_profile(
        db, tenant_id=tenant_id, job_id=job_id
    )
    await db.commit()
    await db.refresh(profile)
    return profile

@router.post(
    "/{dataset_id}/ingest",
    response_model=IngestionJobRead | AgentIngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_ingest(
    dataset_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("dataset:write"))],
) -> IngestionJob | AgentIngestResponse:
    """
    Enqueue an ingestion for an existing pull source (sql, http).

    Returns 202 with a pending job. The worker processes it; poll
    GET /datasets/{dataset_id}/jobs/{job_id} for status.

    Not valid for csv/excel sources — those are populated via /upload,
    which enqueues its own job. Not valid for webhook sources — those
    are enqueued by the webhook receiver.
    """
    source = (
        await db.execute(
            select(DataSource).where(
                DataSource.id == dataset_id,
                DataSource.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=404, detail="dataset not found")

    if source.source_type == "agent":
        return await _trigger_agent_ingest(
            dataset_id=dataset_id, db=db, tenant_id=tenant_id, source=source
        )

    if source.source_type in ("csv", "excel", "pdf"):
        raise HTTPException(
            status_code=400,
            detail="use /upload for file-based sources",
        )
    if source.source_type == "webhook":
        raise HTTPException(
            status_code=400,
            detail="webhook sources are triggered by inbound deliveries",
        )

    try:
        job = await ingestion_service.enqueue_ingest(
            db, tenant_id=tenant_id, source_id=dataset_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await db.commit()
    await db.refresh(job)
    return job


@router.get(
    "/{dataset_id}/observations/classification",
    response_model=ClassificationRead,
)
async def classify_observations_endpoint(
    dataset_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("dataset:read"))],
) -> ClassificationRead:
    source = (
        await db.execute(
            select(DataSource).where(
                DataSource.id == dataset_id,
                DataSource.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    if source.source_type != "agent":
        raise HTTPException(
            status_code=400,
            detail="classification is only available for agent sources",
        )
    result = await agent_classifier.classify_observations(
        db, tenant_id=tenant_id, source_id=dataset_id
    )
    return ClassificationRead(**result.to_dict())


async def _trigger_agent_ingest(
    dataset_id: uuid.UUID,
    db: AsyncSession,
    tenant_id: uuid.UUID,
    source: DataSource,
) -> AgentIngestResponse:
    """
    For an agent source, classify observations and create upload jobs for
    the ready ones. If no observations are ready, returns 400 with the
    classification report.

    One ingestion job is created per ready file. Each file's job completes
    when its content is delivered and ingested. The dashboard shows N jobs.
    """
    agent = (
        await db.execute(
            select(Agent).where(
                Agent.source_id == dataset_id,
                Agent.tenant_id == tenant_id,
                Agent.status == "active",
            )
        )
    ).scalar_one_or_none()
    if agent is None:
        raise HTTPException(
            status_code=400,
            detail="no active agent for this source",
        )

    classification = await agent_classifier.classify_observations(
        db, tenant_id=tenant_id, source_id=dataset_id
    )
    if not classification.ready:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "no files are ready to ingest",
                "classification": classification.to_dict(),
            },
        )

    # Create one ingestion job per ready file and one agent job per file.
    total_agent_jobs = 0
    ingestion_job_ids: list[uuid.UUID] = []
    for obs in classification.ready:
        # Create the ingestion job.
        ingestion_job = await ingestion_service.enqueue_job(
            db, tenant_id=tenant_id, source_id=source.id
        )
        ingestion_job_ids.append(ingestion_job.id)
        ingestion_job.pending_path = obs.path
        ingestion_job.pending_hash = obs.content_hash
        await db.flush()

        agent_jobs = await agent_jobs_service.create_upload_jobs_for_observations(
            db, agent=agent, job=ingestion_job, observations=[obs]
        )
        total_agent_jobs += len(agent_jobs)

    await db.commit()

    return AgentIngestResponse(
        ingestion_job_id=ingestion_job_ids[0],
        agent_job_count=total_agent_jobs,
        classification=classification.to_dict(),
    )