# tests/unit/semantic/test_pack_install.py
import pytest
from sqlalchemy import select

from app.db.models.semantic import (
    CanonicalConcept,
    ConceptRelationship,
    TenantIndustryPack,
)
from app.db.seed_concepts import seed_canonical_concepts
from app.db.seed_packs import seed_industry_packs
from app.db.session import SessionLocal
from app.services.semantic import packs as packs_service


@pytest.mark.asyncio
async def test_seed_and_install_procurement(two_tenants: dict) -> None:
    async with SessionLocal() as db:
        await seed_canonical_concepts(db)
        await seed_industry_packs(db)
        await db.commit()

        # Install for tenant A
        record = await packs_service.install_pack(
            db, tenant_id=two_tenants["tenant_a"], pack_key="procurement.v1"
        )
        await db.commit()
        assert record.pack_key == "procurement.v1"

        # RFQ was introduced by the pack
        rfq = (
            await db.execute(
                select(CanonicalConcept).where(
                    CanonicalConcept.key == "Procurement.RFQ"
                )
            )
        ).scalar_one_or_none()
        assert rfq is not None
        assert rfq.display_name == "Request for Quotation"

        # Relationship between Purchase and Supplier exists
        rel = (
            await db.execute(
                select(ConceptRelationship).where(
                    ConceptRelationship.from_key == "Procurement.Purchase",
                    ConceptRelationship.to_key == "Procurement.Supplier",
                    ConceptRelationship.kind == "belongs_to",
                )
            )
        ).scalar_one_or_none()
        assert rel is not None
        assert rel.cardinality == "N:1"
        assert rel.join_hint == {"left_column": "supplier_id", "right_column": "id"}


@pytest.mark.asyncio
async def test_install_is_idempotent(two_tenants: dict) -> None:
    async with SessionLocal() as db:
        await seed_canonical_concepts(db)
        await seed_industry_packs(db)
        await db.commit()

        r1 = await packs_service.install_pack(
            db, tenant_id=two_tenants["tenant_a"], pack_key="inventory.v1"
        )
        await db.commit()
        r2 = await packs_service.install_pack(
            db, tenant_id=two_tenants["tenant_a"], pack_key="inventory.v1"
        )
        await db.commit()
        assert r1.id == r2.id

        # Only one install record
        count = (
            await db.execute(
                select(TenantIndustryPack).where(
                    TenantIndustryPack.tenant_id == two_tenants["tenant_a"],
                    TenantIndustryPack.pack_key == "inventory.v1",
                )
            )
        ).scalars().all()
        assert len(count) == 1


@pytest.mark.asyncio
async def test_install_unknown_pack_raises(two_tenants: dict) -> None:
    async with SessionLocal() as db:
        with pytest.raises(ValueError, match="unknown pack"):
            await packs_service.install_pack(
                db, tenant_id=two_tenants["tenant_a"], pack_key="nonexistent.v1"
            )