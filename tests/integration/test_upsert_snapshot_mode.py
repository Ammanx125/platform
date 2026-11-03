# tests/integration/test_upsert_snapshot_mode.py
"""
Finding 7: a complete export re-uploaded each week must not be summed.

The reported failure: a tenant uploads sales totals 100k, 120k, 130k for the
same period in append mode and the platform reports 350k when the truth is
130k.
"""

import io

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.db.models.semantic import SemanticMapping
from app.db.seed_concepts import seed_canonical_concepts
from app.db.seed_kpis import seed_kpis
from app.db.session import SessionLocal
from app.services.analytics.kpi import evaluate_kpi
from app.services.ingestion.service import run_job


def _csrf(client: AsyncClient) -> dict[str, str]:
    token = client.cookies.get("sansa_csrf")
    return {"X-CSRF-Token": token} if token else {}


def _csv(total: str, week: str) -> bytes:
    return b"order_id,revenue,order_date\n" + f"W{week}-1,{total},2026-0{week}-01\n".encode()


async def _login(client: AsyncClient, email: str, password: str) -> None:
    await client.post("/api/v1/auth/login", json={"email": email, "password": password})


async def _make_source(client: AsyncClient, *, mode: str, key: str | None):
    payload = {
        "name": f"Sales {mode} {key or 'no-key'}",
        "source_type": "csv",
        "config": {},
    }
    source = (await client.post("/api/v1/datasets", json=payload, headers=_csrf(client))).json()
    async with SessionLocal() as db:
        from app.db.models.dataset import DataSource

        row = (
            await db.execute(select(DataSource).where(DataSource.id == source["id"]))
        ).scalar_one()
        row.snapshot_mode = mode
        row.upsert_key_column = key
        await db.commit()
    return source["id"]


async def _upload(client: AsyncClient, source_id: str, body: bytes) -> str:
    r = await client.post(
        f"/api/v1/datasets/{source_id}/upload",
        files={"file": ("sales.csv", io.BytesIO(body), "text/csv")},
        headers=_csrf(client),
    )
    job_id = r.json()["id"]
    async with SessionLocal() as db:
        await run_job(db, job_id=job_id)
    return job_id


async def _confirm_revenue(source_id, tenant_id) -> None:
    async with SessionLocal() as db:
        row = (
            await db.execute(
                select(SemanticMapping).where(
                    SemanticMapping.source_id == source_id,
                    SemanticMapping.source_column == "revenue",
                )
            )
        ).scalar_one_or_none()
        if row is None:
            await db.execute(select(SemanticMapping))
            from app.db.models.semantic import SemanticMapping as SM

            db.add(
                SM(
                    tenant_id=tenant_id,
                    source_id=source_id,
                    source_column="revenue",
                    canonical_concept_key="Finance.Revenue",
                    status="confirmed",
                    rationale={},
                )
            )
        else:
            row.canonical_concept_key = "Finance.Revenue"
            row.status = "confirmed"
        await db.commit()


@pytest.mark.asyncio
async def test_append_double_counts_a_full_export(client: AsyncClient, two_tenants: dict) -> None:
    """The reported bug, pinned as current append behaviour."""
    async with SessionLocal() as db:
        await seed_canonical_concepts(db)
        await seed_kpis(db)
        await db.commit()
    await _login(client, two_tenants["email_a"], two_tenants["password"])

    source_id = await _make_source(client, mode="append", key=None)
    for total, week in (("100000", 1), ("120000", 2), ("130000", 3)):
        await _upload(client, source_id, _csv(total, str(week)))
    await _confirm_revenue(source_id, two_tenants["tenant_a"])

    async with SessionLocal() as db:
        result = await evaluate_kpi(
            db,
            key="sales.total_revenue",
            tenant_id=two_tenants["tenant_a"],
            source_ids=[source_id],
        )

    assert result.value == pytest.approx(350_000)


@pytest.mark.asyncio
async def test_upsert_keeps_the_newest_record_per_key(
    client: AsyncClient, two_tenants: dict
) -> None:
    """With upsert on the order id, 130k replaces the earlier versions."""
    async with SessionLocal() as db:
        await seed_canonical_concepts(db)
        await seed_kpis(db)
        await db.commit()
    await _login(client, two_tenants["email_a"], two_tenants["password"])

    source_id = await _make_source(client, mode="upsert", key="order_id")

    # The same business record, restated three times across three uploads.
    for total in ("100000", "120000", "130000"):
        await _upload(
            client,
            source_id,
            b"order_id,revenue,order_date\nA-1," + total.encode() + b",2026-01-01\n",
        )
    await _confirm_revenue(source_id, two_tenants["tenant_a"])

    async with SessionLocal() as db:
        result = await evaluate_kpi(
            db,
            key="sales.total_revenue",
            tenant_id=two_tenants["tenant_a"],
            source_ids=[source_id],
        )

    assert result.value == pytest.approx(130_000)


@pytest.mark.asyncio
async def test_upsert_keeps_only_last_duplicate_key_in_same_upload(
    client: AsyncClient, two_tenants: dict
) -> None:
    async with SessionLocal() as db:
        await seed_canonical_concepts(db)
        await seed_kpis(db)
        await db.commit()
    await _login(client, two_tenants["email_a"], two_tenants["password"])

    source_id = await _make_source(client, mode="upsert", key="order_id")
    await _upload(
        client,
        source_id,
        b"order_id,revenue\nA-1,100\nA-1,130\n",
    )
    await _confirm_revenue(source_id, two_tenants["tenant_a"])

    async with SessionLocal() as db:
        result = await evaluate_kpi(
            db,
            key="sales.total_revenue",
            tenant_id=two_tenants["tenant_a"],
            source_ids=[source_id],
        )

    assert result.error is None
    assert result.value == pytest.approx(130)


@pytest.mark.asyncio
async def test_multiple_upsert_sources_keep_their_own_key_columns(
    client: AsyncClient, two_tenants: dict
) -> None:
    async with SessionLocal() as db:
        await seed_canonical_concepts(db)
        await seed_kpis(db)
        await db.commit()
    await _login(client, two_tenants["email_a"], two_tenants["password"])

    order_source = await _make_source(client, mode="upsert", key="order_id")
    invoice_source = await _make_source(client, mode="upsert", key="invoice_id")
    for total in ("100", "130"):
        await _upload(
            client,
            order_source,
            f"order_id,revenue\nO-1,{total}\n".encode(),
        )
    for total in ("50", "70"):
        await _upload(
            client,
            invoice_source,
            f"invoice_id,revenue\nI-1,{total}\n".encode(),
        )
    await _confirm_revenue(order_source, two_tenants["tenant_a"])
    await _confirm_revenue(invoice_source, two_tenants["tenant_a"])

    async with SessionLocal() as db:
        result = await evaluate_kpi(
            db,
            key="sales.total_revenue",
            tenant_id=two_tenants["tenant_a"],
            source_ids=[order_source, invoice_source],
        )

    assert result.error is None
    assert result.value == pytest.approx(200)


@pytest.mark.asyncio
async def test_upsert_requires_a_key_column(client: AsyncClient, two_tenants: dict) -> None:
    """An upsert source with no key must fail loudly, not double-count."""
    async with SessionLocal() as db:
        await seed_canonical_concepts(db)
        await seed_kpis(db)
        await db.commit()
    await _login(client, two_tenants["email_a"], two_tenants["password"])

    source_id = await _make_source(client, mode="upsert", key=None)
    await _upload(client, source_id, _csv("100000", "1"))
    await _confirm_revenue(source_id, two_tenants["tenant_a"])

    async with SessionLocal() as db:
        result = await evaluate_kpi(
            db,
            key="sales.total_revenue",
            tenant_id=two_tenants["tenant_a"],
            source_ids=[source_id],
        )

    assert result.error is not None
    assert "key column" in result.error
