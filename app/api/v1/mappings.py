# app/api/v1/mappings.py
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentTenantId, CurrentUser, require_permission
from app.db.models.dataset import DataSource
from app.db.models.semantic import CanonicalConcept, SemanticMapping
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.semantic import (
    CanonicalConceptRead,
    ProposeMappingsResponse,
    SemanticMappingCreate,
    SemanticMappingPatch,
    SemanticMappingRead,
)
from app.services.understanding import mapper as mapper_service

router = APIRouter(tags=["mappings"])


async def _get_source_or_404(
    db: AsyncSession, *, source_id: uuid.UUID, tenant_id: uuid.UUID
) -> DataSource:
    source = (
        await db.execute(
            select(DataSource).where(
                DataSource.id == source_id,
                DataSource.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=404, detail="dataset not found")
    return source


# ---------- concepts (global catalog, read-only) ----------

@router.get(
    "/concepts",
    response_model=list[CanonicalConceptRead],
)
async def list_concepts(
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(require_permission("knowledge:read"))],
) -> list[CanonicalConcept]:
    stmt = select(CanonicalConcept).order_by(CanonicalConcept.domain, CanonicalConcept.key)
    return list((await db.execute(stmt)).scalars().all())


# ---------- mappings for a source ----------

@router.get(
    "/datasets/{dataset_id}/mappings",
    response_model=list[SemanticMappingRead],
)
async def list_mappings(
    dataset_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("knowledge:read"))],
) -> list[SemanticMapping]:
    await _get_source_or_404(db, source_id=dataset_id, tenant_id=tenant_id)
    stmt = (
        select(SemanticMapping)
        .where(
            SemanticMapping.source_id == dataset_id,
            SemanticMapping.tenant_id == tenant_id,
        )
        .order_by(SemanticMapping.source_column)
    )
    return list((await db.execute(stmt)).scalars().all())


@router.post(
    "/datasets/{dataset_id}/mappings/propose",
    response_model=ProposeMappingsResponse,
)
async def propose_mappings(
    dataset_id: uuid.UUID,
    job_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    _user: Annotated[User, Depends(require_permission("dataset:write"))],
) -> ProposeMappingsResponse:
    """
    Run the deterministic matcher against a job's profile and persist
    proposals. `job_id` is a query parameter.
    """
    await _get_source_or_404(db, source_id=dataset_id, tenant_id=tenant_id)
    try:
        created = await mapper_service.propose_mappings(db, job_id=job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.commit()
    mappings = [SemanticMappingRead.model_validate(mapping) for mapping in created]
    return ProposeMappingsResponse(created=len(mappings), mappings=mappings)


@router.post(
    "/datasets/{dataset_id}/mappings",
    response_model=SemanticMappingRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_mapping(
    dataset_id: uuid.UUID,
    body: SemanticMappingCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    user: CurrentUser,
    _perm: Annotated[User, Depends(require_permission("dataset:write"))],
) -> SemanticMapping:
    await _get_source_or_404(db, source_id=dataset_id, tenant_id=tenant_id)

    # Validate the concept exists
    concept = (
        await db.execute(
            select(CanonicalConcept).where(
                CanonicalConcept.key == body.canonical_concept_key
            )
        )
    ).scalar_one_or_none()
    if concept is None:
        raise HTTPException(status_code=400, detail="unknown canonical concept")

    # Reject if a mapping already exists for this (source, column)
    existing = (
        await db.execute(
            select(SemanticMapping).where(
                SemanticMapping.source_id == dataset_id,
                SemanticMapping.source_column == body.source_column,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail="mapping already exists for this column; use PATCH to change it",
        )

    mapping = SemanticMapping(
        tenant_id=tenant_id,
        source_id=dataset_id,
        source_column=body.source_column,
        canonical_concept_key=body.canonical_concept_key,
        status="confirmed",             # human-authored is confirmed by default
        confidence=None,
        rationale={"method": "manual", "by_user_id": str(user.id)},
        confirmed_by_user_id=user.id,
        confirmed_at=datetime.now(UTC),
    )
    db.add(mapping)
    await db.commit()
    await db.refresh(mapping)
    return mapping


@router.patch(
    "/datasets/{dataset_id}/mappings/{mapping_id}",
    response_model=SemanticMappingRead,
)
async def update_mapping(
    dataset_id: uuid.UUID,
    mapping_id: uuid.UUID,
    body: SemanticMappingPatch,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: CurrentTenantId,
    user: CurrentUser,
    _perm: Annotated[User, Depends(require_permission("dataset:write"))],
) -> SemanticMapping:
    await _get_source_or_404(db, source_id=dataset_id, tenant_id=tenant_id)

    mapping = (
        await db.execute(
            select(SemanticMapping).where(
                SemanticMapping.id == mapping_id,
                SemanticMapping.source_id == dataset_id,
                SemanticMapping.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if mapping is None:
        raise HTTPException(status_code=404, detail="mapping not found")

    if body.canonical_concept_key is not None:
        concept = (
            await db.execute(
                select(CanonicalConcept).where(
                    CanonicalConcept.key == body.canonical_concept_key
                )
            )
        ).scalar_one_or_none()
        if concept is None:
            raise HTTPException(status_code=400, detail="unknown canonical concept")
        mapping.canonical_concept_key = body.canonical_concept_key

    if body.status is not None:
        mapping.status = body.status
        if body.status == "confirmed":
            mapping.confirmed_by_user_id = user.id
            mapping.confirmed_at = datetime.now(UTC)
        elif body.status in ("rejected", "proposed"):
            mapping.confirmed_by_user_id = None
            mapping.confirmed_at = None

    await db.commit()
    await db.refresh(mapping)
    return mapping