# tests/integration/test_webhook_ingest.py
import hashlib
import hmac
import json

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.db.models.dataset import IngestionJob, StagedRow
from app.db.session import SessionLocal
from app.services.ingestion.service import run_job


def _csrf(client: AsyncClient) -> dict[str, str]:
    t = client.cookies.get("sansa_csrf")
    return {"X-CSRF-Token": t} if t else {}


async def _login(client: AsyncClient, email: str, password: str) -> None:
    await client.post("/api/v1/auth/login", json={"email": email, "password": password})


@pytest.mark.asyncio
async def test_webhook_end_to_end(client: AsyncClient, two_tenants: dict) -> None:
    await _login(client, two_tenants["email_a"], two_tenants["password"])

    # create webhook source
    r = await client.post(
        "/api/v1/datasets/webhook",
        json={"name": "Test webhook"},
        headers=_csrf(client),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    token = body["token"]
    secret = body["secret"]
    source_id = body["id"]

    # send a webhook delivery with correct signature
    payload = {"event": "purchase", "amount": 42, "supplier": "ACME"}
    raw = json.dumps(payload).encode("utf-8")
    sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()

    r = await client.post(
        f"/api/v1/webhooks/{token}",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Sansa-Signature": sig,
        },
    )
    assert r.status_code == 202, r.text

    # worker processes the pending job
    async with SessionLocal() as db:
        job = (
            await db.execute(
                select(IngestionJob).where(
                    IngestionJob.source_id == source_id,
                    IngestionJob.status == "pending",
                )
            )
        ).scalar_one()
        await run_job(db, job_id=job.id)

    # verify staged row
    async with SessionLocal() as db:
        staged = (
            await db.execute(
                select(StagedRow).where(StagedRow.source_id == source_id)
            )
        ).scalars().all()
        assert len(staged) == 1
        assert staged[0].raw_data["event"] == "purchase"


@pytest.mark.asyncio
async def test_webhook_rejects_bad_signature(client: AsyncClient, two_tenants: dict) -> None:
    await _login(client, two_tenants["email_a"], two_tenants["password"])

    r = await client.post(
        "/api/v1/datasets/webhook",
        json={"name": "Bad sig test"},
        headers=_csrf(client),
    )
    token = r.json()["token"]

    r = await client.post(
        f"/api/v1/webhooks/{token}",
        content=b'{"x":1}',
        headers={"X-Sansa-Signature": "deadbeef"},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_webhook_rejects_unknown_token(client: AsyncClient, two_tenants: dict) -> None:
    r = await client.post(
        "/api/v1/webhooks/not-a-real-token",
        content=b'{"x":1}',
        headers={"X-Sansa-Signature": "deadbeef"},
    )
    assert r.status_code == 401