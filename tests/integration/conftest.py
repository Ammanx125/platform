# tests/integration/conftest.py
import io
import uuid

import pytest_asyncio
from sqlalchemy import select

from app.db.models.semantic import SemanticMapping
from app.db.seed_concepts import seed_canonical_concepts
from app.db.seed_kpis import seed_kpis
from app.db.session import SessionLocal
from app.services.ingestion.service import run_job

CSV = (
    b"supplier,product,quantity,unit_price,order_date\n"
    b"Acme,Bolt,100,0.42,2026-01-15\n"
    b"Acme,Nut,200,0.18,2026-01-15\n"
    b"Beacon,Sheet,50,18.75,2026-01-20\n"
)

# The mappings the spend KPIs need, as (column, concept). They are pinned
# explicitly rather than read back from the matcher's proposals: in the full
# suite the catalog has accumulated concepts from earlier modules, and the
# matcher can bind 'unit_price' to something else (e.g.
# Management.BusinessUnit via fuzzy match). A KPI test should exercise KPI
# evaluation, not the matcher's tie-breaking.
PINNED_MAPPINGS = {
    "supplier": "Procurement.Supplier",
    "unit_price": "Procurement.PurchasePrice",
}


def _csrf(client) -> dict[str, str]:
    token = client.cookies.get("sansa_csrf")
    return {"X-CSRF-Token": token} if token else {}


@pytest_asyncio.fixture
async def procurement_tenant(client, two_tenants):
    """
    A tenant with the procurement CSV ingested and the spend mappings
    *confirmed*.

    The canonical concept catalog is global and `seed_canonical_concepts`
    is additive, and the matcher has a tie between Procurement.Supplier and
    Sales.Customer for the column 'customer': whichever module seeds last
    decides how the next module's proposal lands. Test modules run in
    alphabetical order, so this fixture re-confirms the mappings it needs
    after proposing, instead of assuming a previous module left them
    confirmed.

    Yields (tenants, source_id). The source and its mappings cascade on
    tenant teardown, so there is nothing to undo here.
    """
    async with SessionLocal() as db:
        await seed_canonical_concepts(db)
        await seed_kpis(db)
        await db.commit()

    await client.post(
        "/api/v1/auth/login",
        json={
            "email": two_tenants["email_a"],
            "password": two_tenants["password"],
        },
    )
    headers = _csrf(client)

    r = await client.post(
        "/api/v1/datasets",
        json={"name": "KPI metadata", "source_type": "csv", "config": {}},
        headers=headers,
    )
    ds_id = r.json()["id"]
    # A dataset is backed by a DataSource with the same id.
    source_id = uuid.UUID(ds_id)

    r = await client.post(
        f"/api/v1/datasets/{ds_id}/upload",
        files={"file": ("data.csv", io.BytesIO(CSV), "text/csv")},
        headers=headers,
    )
    job_id = r.json()["id"]

    async with SessionLocal() as db:
        await run_job(db, job_id=job_id)

    await client.post(
        f"/api/v1/datasets/{ds_id}/mappings/propose",
        params={"job_id": job_id},
        headers=headers,
    )

    async with SessionLocal() as db:
        rows = (
            (
                await db.execute(
                    select(SemanticMapping).where(
                        SemanticMapping.tenant_id == two_tenants["tenant_a"],
                        SemanticMapping.source_id == source_id,
                        SemanticMapping.source_column.in_(PINNED_MAPPINGS),
                    )
                )
            )
            .scalars()
            .all()
        )
        by_column = {m.source_column: m for m in rows}
        for column, concept_key in PINNED_MAPPINGS.items():
            mapping = by_column.get(column)
            if mapping is None:
                mapping = SemanticMapping(
                    tenant_id=two_tenants["tenant_a"],
                    source_id=source_id,
                    source_column=column,
                    canonical_concept_key=concept_key,
                    rationale={},
                )
                db.add(mapping)
            else:
                mapping.canonical_concept_key = concept_key
            mapping.status = "confirmed"
            mapping.confidence = 1.0
        await db.commit()

    yield two_tenants, source_id
