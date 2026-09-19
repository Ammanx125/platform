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
from app.db.models.dataset import DataSource, IngestionJob
from app.db.models.understanding import DataProfile
from app.db.session import get_db
from app.schemas.dataset import DataSourceCreate, DataSourceRead, IngestionJobRead
from app.schemas.understanding import DataProfileRead
from app.services.ingestion import service as ingestion_service
from app.services.ingestion.base import IngestionError
from app.services.storage.local import storage
from app.services.understanding import service as understanding_service

router = APIRouter(prefix="/datasets", tags=["datasets"])


@router.get("", response_model=list[DataSourceRead])
async def list_datasets(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[object, Depends(require_permission("dataset:read"))],
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
    _user: Annotated[object, Depends(require_permission("dataset:write"))],
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
    _user: Annotated[object, Depends(require_permission("dataset:read"))],
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
    _user: Annotated[object, Depends(require_permission("dataset:write"))],
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

    if not file.filename:
        raise HTTPException(status_code=400, detail="no filename")

    ext = ingestion_service.extension_of(file.filename)
    if ext not in settings.allowed_upload_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"extension not allowed: {ext}",
        )

    content = await file.read()
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"file too large (max {settings.max_upload_bytes} bytes)",
        )

    # Derive source_type from extension. Overrides whatever was passed at create.
    try:
        source_type = ingestion_service.extension_to_source_type(ext)
    except IngestionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    source.source_type = source_type

    storage_key = ingestion_service.build_storage_key(
        tenant_id=tenant_id,
        source_id=source.id,
        extension=ext,
    )
    await storage.put(key=storage_key, content=content)

    source.config = {**source.config, "storage_key": storage_key}

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
    _user: Annotated[object, Depends(require_permission("dataset:read"))],
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
    _user: Annotated[object, Depends(require_permission("dataset:read"))],
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
    _user: Annotated[object, Depends(require_permission("dataset:read"))],
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
    "/{dataset_id}/jobs/{job_id}/profile",
    response_model=DataProfileRead,
)
async def recompute_job_profile(
    dataset_id: uuid.UUID,
    job_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[object, Depends(require_permission("dataset:write"))],
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

    profile = await understanding_service.compute_profile(db, job_id=job_id)
    await db.commit()
    await db.refresh(profile)
    return profile