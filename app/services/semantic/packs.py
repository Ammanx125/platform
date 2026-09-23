# app/services/semantic/packs.py
"""
Industry pack installation and listing.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.semantic import (
    CanonicalConcept,
    ConceptRelationship,
    IndustryPack,
    TenantIndustryPack,
)


async def list_available_packs(db: AsyncSession) -> list[IndustryPack]:
    stmt = select(IndustryPack).order_by(IndustryPack.key)
    return list((await db.execute(stmt)).scalars().all())


async def list_installed_packs(
    db: AsyncSession, *, tenant_id: uuid.UUID
) -> list[tuple[IndustryPack, TenantIndustryPack]]:
    """
    Returns [(pack, install_record)] for the tenant.
    """
    stmt = (
        select(IndustryPack, TenantIndustryPack)
        .join(TenantIndustryPack, TenantIndustryPack.pack_key == IndustryPack.key)
        .where(TenantIndustryPack.tenant_id == tenant_id)
        .order_by(IndustryPack.key)
    )
    rows = (await db.execute(stmt)).all()
    return [(row[0], row[1]) for row in rows]


async def _materialize_concepts(
    db: AsyncSession, *, pack: IndustryPack
) -> None:
    """
    Ensure every concept declared by the pack exists. Concepts defined only
    by key ({"key": "..."}) must already exist in the catalog — we don't
    fabricate them. Concepts with full definitions are upserted.
    """
    keys = [c["key"] for c in pack.concepts]
    existing = {
        c.key: c
        for c in (
            await db.execute(
                select(CanonicalConcept).where(CanonicalConcept.key.in_(keys))
            )
        ).scalars().all()
    }

    missing: list[str] = []
    for spec in pack.concepts:
        key = spec["key"]
        if key in existing:
            # If the pack provides a fuller definition, update mutable fields.
            if "display_name" in spec:
                c = existing[key]
                c.display_name = spec["display_name"]
                c.domain = spec.get("domain", c.domain)
                c.kind = spec.get("kind", c.kind)
                c.value_type = spec.get("value_type", c.value_type)
                c.synonyms = spec.get("synonyms", c.synonyms)
                c.description = spec.get("description", c.description)
            continue
        if "display_name" not in spec:
            missing.append(key)
            continue
        db.add(CanonicalConcept(
            key=key,
            display_name=spec["display_name"],
            domain=spec["domain"],
            kind=spec["kind"],
            value_type=spec["value_type"],
            synonyms=spec.get("synonyms", []),
            description=spec.get("description"),
        ))

    await db.flush()

    if missing:
        raise ValueError(
            f"pack {pack.key!r} references unknown concepts without a "
            f"definition: {missing}"
        )


async def _materialize_relationships(
    db: AsyncSession, *, pack: IndustryPack
) -> None:
    """
    Ensure every relationship declared by the pack exists. Idempotent.
    """
    for spec in pack.relationships:
        stmt = select(ConceptRelationship).where(
            ConceptRelationship.from_key == spec["from_key"],
            ConceptRelationship.to_key == spec["to_key"],
            ConceptRelationship.kind == spec["kind"],
        )
        existing = (await db.execute(stmt)).scalar_one_or_none()
        if existing is None:
            db.add(ConceptRelationship(
                from_key=spec["from_key"],
                to_key=spec["to_key"],
                kind=spec["kind"],
                cardinality=spec.get("cardinality", "1:N"),
                join_hint=spec.get("join_hint"),
                description=spec.get("description"),
            ))
        else:
            existing.cardinality = spec.get("cardinality", existing.cardinality)
            existing.join_hint = spec.get("join_hint", existing.join_hint)
            existing.description = spec.get("description", existing.description)

    await db.flush()


async def install_pack(
    db: AsyncSession, *, tenant_id: uuid.UUID, pack_key: str
) -> TenantIndustryPack:
    """
    Materialize a pack's concepts and relationships, then record the
    installation for the tenant.

    Idempotent: installing twice is a no-op for concepts/relationships and
    returns the existing install record.
    """
    pack = (
        await db.execute(select(IndustryPack).where(IndustryPack.key == pack_key))
    ).scalar_one_or_none()
    if pack is None:
        raise ValueError(f"unknown pack: {pack_key!r}")

    await _materialize_concepts(db, pack=pack)
    await _materialize_relationships(db, pack=pack)

    existing = (
        await db.execute(
            select(TenantIndustryPack).where(
                TenantIndustryPack.tenant_id == tenant_id,
                TenantIndustryPack.pack_key == pack_key,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    record = TenantIndustryPack(
        tenant_id=tenant_id,
        pack_key=pack_key,
        enabled_at=datetime.now(UTC),
    )
    db.add(record)
    await db.flush()
    return record