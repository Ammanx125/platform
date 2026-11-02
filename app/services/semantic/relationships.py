# app/services/semantic/relationships.py
"""
Which concepts can be analysed together.

There is no join specification here. Sansa answers the narrower question
the dashboard needs: two concepts can be grouped or filtered against each
other when they are mapped on the *same source*. That is a property of
SemanticMapping rows, not of the concept catalog, and it is tenant-scoped
because mappings are.

`ConceptRelationship` still describes catalog-level semantics
(Purchase belongs_to Supplier); this module never consults it.
"""

from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.semantic import SemanticMapping


async def concepts_in_same_source(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    concept_keys: list[str],
) -> dict[uuid.UUID, list[str]]:
    """
    Return {source_id: [concept_keys mapped to that source]}.

    Only confirmed mappings count: a proposed mapping has not been accepted
    as a meaning yet, so grouping on it would produce a chart nobody
    vouched for. Sources with no overlap with `concept_keys` are omitted,
    and concept keys are returned in the order they were requested.

    The result says "these concepts are co-located", not "these concepts
    are joinable". Two columns on one source row can be grouped; columns on
    different sources cannot be combined without a join Sansa does not model.
    """
    if not concept_keys:
        return {}

    requested = list(dict.fromkeys(concept_keys))
    rows = (
        await db.execute(
            select(
                SemanticMapping.source_id,
                SemanticMapping.canonical_concept_key,
            ).where(
                SemanticMapping.tenant_id == tenant_id,
                SemanticMapping.status == "confirmed",
                SemanticMapping.canonical_concept_key.in_(requested),
            )
        )
    ).all()

    present: dict[uuid.UUID, set[str]] = defaultdict(set)
    for source_id, concept_key in rows:
        present[source_id].add(concept_key)

    return {
        source_id: [key for key in requested if key in found]
        for source_id, found in present.items()
    }


async def groupable_concepts(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    concept_key: str,
    candidates: list[str],
) -> list[str]:
    """
    The subset of `candidates` that can be grouped with `concept_key`.

    A candidate qualifies when it is confirmed on at least one source that
    also confirms `concept_key`. Returned in the order given.
    """
    by_source = await concepts_in_same_source(
        db, tenant_id=tenant_id, concept_keys=[concept_key, *candidates]
    )
    groupable = {
        key
        for keys in by_source.values()
        if concept_key in keys
        for key in keys
        if key != concept_key
    }
    return [key for key in dict.fromkeys(candidates) if key in groupable]
