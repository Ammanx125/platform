# app/services/understanding/mapper.py
"""
Orchestration for semantic mapping: load the profile, run the matcher,
persist proposals.

Rules:
  - Only propose mappings for columns that don't already have one.
  - Existing mappings (any status) are never overwritten by a re-run.
    Human decisions are sticky.
  - Columns the matcher can't confidently resolve are silently skipped.
    A future "unmapped columns" view surfaces them for review.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.dataset import DataSource, IngestionJob
from app.db.models.semantic import CanonicalConcept, SemanticMapping
from app.db.models.understanding import DataProfile
from app.services.understanding.matcher import (
    DeterministicMatcher,
    MappingProposal,
    SemanticMatcher,
)


async def _load_columns(db: AsyncSession, *, job_id: uuid.UUID) -> list[str]:
    profile = (
        await db.execute(
            select(DataProfile).where(DataProfile.job_id == job_id)
        )
    ).scalar_one_or_none()
    if profile is None:
        raise ValueError(
            f"no profile for job {job_id}; run the profiler first"
        )
    columns: list[str] = [
        str(c["name"]) for c in profile.profile.get("columns", [])
    ]
    return columns


async def propose_mappings(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    matcher: SemanticMatcher | None = None,
) -> list[SemanticMapping]:
    """
    Run the matcher against the columns of a job's profile and persist new
    proposals. Returns the newly created SemanticMapping rows.

    Existing mappings for the source are left untouched.
    """
    job = (
        await db.execute(select(IngestionJob).where(IngestionJob.id == job_id))
    ).scalar_one_or_none()
    if job is None:
        raise ValueError(f"job not found: {job_id}")

    source = (
        await db.execute(select(DataSource).where(DataSource.id == job.source_id))
    ).scalar_one_or_none()
    if source is None:
        raise ValueError(f"source not found: {job.source_id}")

    columns = await _load_columns(db, job_id=job_id)
    if not columns:
        return []

    existing_cols: set[str] = set(
        (
            await db.execute(
                select(SemanticMapping.source_column).where(
                    SemanticMapping.source_id == source.id
                )
            )
        ).scalars().all()
    )

    concepts: list[CanonicalConcept] = list(
        (await db.execute(select(CanonicalConcept))).scalars().all()
    )
    if not concepts:
        raise ValueError(
            "canonical concept catalog is empty; run scripts.seed_concepts"
        )

    active_matcher: SemanticMatcher = matcher or DeterministicMatcher()
    proposals: list[MappingProposal] = await active_matcher.propose(
        columns=columns, concepts=concepts
    )

    created: list[SemanticMapping] = []
    for p in proposals:
        if p.source_column in existing_cols:
            continue
        row = SemanticMapping(
            tenant_id=job.tenant_id,
            source_id=source.id,
            source_column=p.source_column,
            canonical_concept_key=p.canonical_concept_key,
            status="proposed",
            confidence=p.confidence,
            rationale=p.rationale,
        )
        db.add(row)
        created.append(row)

    await db.flush()
    return created