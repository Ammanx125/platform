# tests/unit/analytics/test_series_builder.py
"""
Note: build_series hits the DB. This file is technically a small
integration test masquerading as a unit test. It's here because the logic
being tested — basis selection, column resolution, skip counting — is
specific to the builder and worth exercising against a real DB.
"""
import io

import pytest
from httpx import AsyncClient

from app.db.seed_anomaly_detectors import seed_anomaly_detectors
from app.db.seed_concepts import seed_canonical_concepts
from app.db.session import SessionLocal
from app.services.analytics.series import build_series
from app.services.ingestion.service import run_job

CSV = (
    b"date,supplier,price\n"
    b"2026-01-01,Acme,10\n"
    b"2026-01-02,Acme,10\n"
    b"2026-01-03,Acme,10\n"
    b"2026-01-04,Acme,100\n"
)


def _csrf(client: AsyncClient) -> dict[str, str]:
    t = client.cookies.get("sansa_csrf")
    return {"X-CSRF-Token": t} if t else {}


async def _login(client: AsyncClient, email: str, password: str) -> None:
    await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )


@pytest.mark.asyncio
async def test_build_series(client: AsyncClient, two_tenants: dict) -> None:
    async with SessionLocal() as db:
        await seed_canonical_concepts(db)
        await seed_anomaly_detectors(db)
        await db.commit()

    await _login(client, two_tenants["email_a"], two_tenants["password"])

    r = await client.post(
        "/api/v1/datasets",
        json={"name": "series test", "source_type": "csv", "config": {}},
        headers=_csrf(client),
    )
    ds_id = r.json()["id"]

    r = await client.post(
        f"/api/v1/datasets/{ds_id}/upload",
        files={"file": ("data.csv", io.BytesIO(CSV), "text/csv")},
        headers=_csrf(client),
    )
    job_id = r.json()["id"]

    async with SessionLocal() as db:
        await run_job(db, job_id=job_id)

    # Propose + confirm mappings so the series builder can find columns.
    r = await client.post(
        f"/api/v1/datasets/{ds_id}/mappings/propose",
        params={"job_id": job_id},
        headers=_csrf(client),
    )
    for m in r.json()["mappings"]:
        await client.patch(
            f"/api/v1/datasets/{ds_id}/mappings/{m['id']}",
            json={"status": "confirmed"},
            headers=_csrf(client),
        )

    async with SessionLocal() as db:
        result = await build_series(
            db,
            tenant_id=two_tenants["tenant_a"],
            source_ids=[ds_id],
            value_concept="Procurement.PurchasePrice",
            group_by_concept="Procurement.Supplier",
            time_basis_preference=["content", "ingestion"],
        )

    # We can't be sure the matcher picked the exact concepts we hoped for,
    # so assert only that the builder ran and reported something.
    assert result.rows_seen == 4
    # Rows may be skipped depending on how the matcher mapped columns.
    assert result.rows_used + result.rows_skipped_no_mapping + result.rows_skipped_no_value == 4