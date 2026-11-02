# scripts/reseed_concept_metadata.py
"""
Backfill measure metadata for concepts installed from industry packs.

`seed_canonical_concepts` only knows the base catalog. Transport concepts
(Transport.FuelUseRate and friends) live in packs. Pack specs are stored on
IndustryPack rows at seed time, so the order matters:

  1. seed_canonical_concepts  — base catalog unit/currency
  2. seed_industry_packs      — refresh IndustryPack rows from PACKS
  3. install_pack per tenant  — materialize concepts from those rows

Skipping step 2 silently leaves pack concepts unlabelled (the row still
holds the pre-metadata spec). This is the same code path tenants take
normally; it is idempotent and never deletes.

Run once after the concept-measure-metadata migration:

    python -m scripts.reseed_concept_metadata
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.db.models.semantic import CanonicalConcept, TenantIndustryPack
from app.db.seed_concepts import seed_canonical_concepts
from app.db.seed_packs import seed_industry_packs
from app.db.session import SessionLocal
from app.services.semantic import packs as packs_service


async def main() -> None:
    async with SessionLocal() as db:
        reconciled = await seed_canonical_concepts(db)
        packs_reconciled = await seed_industry_packs(db)

        pack_keys = set(
            (await db.execute(select(TenantIndustryPack.pack_key).distinct())).scalars().all()
        )
        installed: list[str] = []
        for pack_key in sorted(pack_keys):
            tenant_ids = (
                (
                    await db.execute(
                        select(TenantIndustryPack.tenant_id).where(
                            TenantIndustryPack.pack_key == pack_key
                        )
                    )
                )
                .scalars()
                .all()
            )
            for tenant_id in tenant_ids:
                await packs_service.install_pack(db, tenant_id=tenant_id, pack_key=pack_key)
            installed.append(pack_key)

        await db.commit()

        labelled = (
            (
                await db.execute(
                    select(CanonicalConcept)
                    .where(CanonicalConcept.unit.is_not(None))
                    .order_by(CanonicalConcept.key)
                )
            )
            .scalars()
            .all()
        )

        print(f"reconciled {reconciled} base concepts")
        print(f"reconciled {packs_reconciled} industry packs")
        print(f"re-installed packs: {installed or 'none'}")
        print(f"concepts with a unit: {len(labelled)}")
        for concept in labelled:
            print(f"  {concept.key:34} {concept.unit:18} {concept.currency or ''}")


if __name__ == "__main__":
    asyncio.run(main())
