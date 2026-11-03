# tests/concurrency/test_workflow_idempotency_race.py
"""
Race: two callers start the same workflow with the same idempotency key.

Invariant: exactly one WorkflowInstance is created for (tenant, key).
Both callers get the same instance row.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models.dataset import DataSource
from app.db.models.semantic import SemanticMapping
from app.db.models.workflow import WorkflowInstance
from app.db.seed_concepts import seed_canonical_concepts
from app.db.session import SessionLocal
from app.services.workflows import engine as workflow_engine

pytestmark = pytest.mark.concurrency


@pytest.mark.asyncio
async def test_concurrent_idempotent_start(concurrency_env: dict) -> None:
    tenant_a = concurrency_env["tenant_a"]
    user_a = concurrency_env["user_a"]
    key = "race-idempotency-1"

    async with SessionLocal() as db:
        await seed_canonical_concepts(db)
        source = DataSource(
            tenant_id=tenant_a,
            name="Idempotency race source",
            source_type="csv",
            config={},
        )
        db.add(source)
        await db.flush()
        db.add(
            SemanticMapping(
                tenant_id=tenant_a,
                source_id=source.id,
                source_column="purchase_price",
                canonical_concept_key="Procurement.PurchasePrice",
                status="confirmed",
                rationale={},
            )
        )
        await db.commit()

    from tests.concurrency.helpers import run_concurrently

    async def _start():
        async with SessionLocal() as db:
            inst = await workflow_engine.start_workflow(
                db,
                workflow_key="procurement.spend_analysis",
                tenant_id=tenant_a,
                user_id=user_a,
                run_now=False,
                idempotency_key=key,
            )
            await db.commit()
            return inst.id

    results = await run_concurrently([_start, _start, _start])

    assert all(r.succeeded for r in results), [
        repr(result.exception) for result in results if not result.succeeded
    ]

    ids = {r.value for r in results}
    assert len(ids) == 1

    async with SessionLocal() as db:
        rows = (
            (
                await db.execute(
                    select(WorkflowInstance).where(
                        WorkflowInstance.tenant_id == tenant_a,
                        WorkflowInstance.idempotency_key == key,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
