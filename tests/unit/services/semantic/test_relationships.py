# tests/unit/services/semantic/test_relationships.py
import uuid

import pytest

from app.db.models.dataset import DataSource
from app.db.models.semantic import SemanticMapping
from app.db.session import SessionLocal
from app.services.semantic.relationships import (
    concepts_in_same_source,
    groupable_concepts,
)


async def _add_source(db, tenant_id, name: str) -> uuid.UUID:
    source = DataSource(
        tenant_id=tenant_id,
        name=name,
        source_type="csv",
        config={},
        is_active=True,
    )
    db.add(source)
    await db.flush()
    return source.id


async def _map(db, tenant_id, source_id, column, concept_key, status="confirmed"):
    db.add(
        SemanticMapping(
            tenant_id=tenant_id,
            source_id=source_id,
            source_column=column,
            canonical_concept_key=concept_key,
            status=status,
            rationale={},
        )
    )
    await db.flush()


@pytest.mark.asyncio
async def test_concepts_in_same_source_groups_by_source(two_tenants) -> None:
    tenant = two_tenants["tenant_a"]
    async with SessionLocal() as db:
        fares = await _add_source(db, tenant, "tickets")
        fuel = await _add_source(db, tenant, "fuel")

        await _map(db, tenant, fares, "Revenue", "Finance.Revenue")
        await _map(db, tenant, fares, "Route", "Transport.Route")
        await _map(db, tenant, fuel, "Fuel Use Rate", "Transport.FuelUseRate")
        await db.commit()

        result = await concepts_in_same_source(
            db,
            tenant_id=tenant,
            concept_keys=[
                "Finance.Revenue",
                "Transport.Route",
                "Transport.FuelUseRate",
            ],
        )

    assert result[fares] == ["Finance.Revenue", "Transport.Route"]
    assert result[fuel] == ["Transport.FuelUseRate"]


@pytest.mark.asyncio
async def test_proposed_mappings_do_not_count(two_tenants) -> None:
    tenant = two_tenants["tenant_a"]
    async with SessionLocal() as db:
        source = await _add_source(db, tenant, "draft")
        await _map(db, tenant, source, "Revenue", "Finance.Revenue")
        await _map(db, tenant, source, "Cost", "Finance.Cost", status="proposed")
        await db.commit()

        result = await concepts_in_same_source(
            db,
            tenant_id=tenant,
            concept_keys=["Finance.Revenue", "Finance.Cost"],
        )

    assert result[source] == ["Finance.Revenue"]


@pytest.mark.asyncio
async def test_other_tenants_mappings_are_invisible(two_tenants) -> None:
    tenant_a = two_tenants["tenant_a"]
    tenant_b = two_tenants["tenant_b"]
    async with SessionLocal() as db:
        source = await _add_source(db, tenant_b, "b-source")
        await _map(db, tenant_b, source, "Revenue", "Finance.Revenue")
        await db.commit()

        result = await concepts_in_same_source(
            db, tenant_id=tenant_a, concept_keys=["Finance.Revenue"]
        )

    assert result == {}


@pytest.mark.asyncio
async def test_empty_request_returns_empty_without_querying(two_tenants) -> None:
    async with SessionLocal() as db:
        assert (
            await concepts_in_same_source(db, tenant_id=two_tenants["tenant_a"], concept_keys=[])
            == {}
        )


@pytest.mark.asyncio
async def test_groupable_concepts_requires_shared_source(two_tenants) -> None:
    tenant = two_tenants["tenant_a"]
    async with SessionLocal() as db:
        shared = await _add_source(db, tenant, "shared")
        solo = await _add_source(db, tenant, "solo")

        await _map(db, tenant, shared, "Revenue", "Finance.Revenue")
        await _map(db, tenant, shared, "Route", "Transport.Route")
        await _map(db, tenant, solo, "Fuel", "Transport.FuelUseRate")
        await db.commit()

        groupable = await groupable_concepts(
            db,
            tenant_id=tenant,
            concept_key="Finance.Revenue",
            candidates=[
                "Transport.Route",
                "Transport.FuelUseRate",
                "Sales.Customer",
            ],
        )

    assert groupable == ["Transport.Route"]
