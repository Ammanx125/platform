# app/services/ingestion/webhook.py
"""
Webhook receiver support.

Unlike pull connectors, webhooks are pushed to Sansa. Design:

  1. Customer creates a webhook source via POST /api/v1/datasets/webhook.
     Sansa generates a token (URL routing) and a signing secret (HMAC).
     Both are returned ONCE. Stored:
       - token: SHA-256 hash (irreversible, only needs lookup)
       - secret: Fernet ciphertext (reversible, needed for HMAC verification)

  2. Customer configures their sender:
       URL:      https://<sansa>/api/v1/webhooks/<token>
       Header:   X-Sansa-Signature: <hex(HMAC-SHA256(secret, raw_body))>

  3. On receipt:
       - Hash the URL token, find the source. 401 if not found.
       - Decrypt the source's signing secret.
       - Verify HMAC. 401 if mismatched.
       - Persist a WebhookDelivery row and (coalesced) IngestionJob.

  4. The worker processes pending deliveries for the source, turning each
     into a StagedRow.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets as pysecrets
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.db.models.dataset import DataSource, IngestionJob
from app.db.models.webhook import WebhookDelivery
from app.services.ingestion.base import (
    IngestionResult,
    ParsedRow,
    SourceMetadata,
)

# ---- token / secret generation --------------------------------------------

def generate_token() -> str:
    return pysecrets.token_urlsafe(32)


def generate_signing_secret() -> str:
    return pysecrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---- signature verification -----------------------------------------------

def compute_signature(*, raw_body: bytes, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()


def verify_signature(*, raw_body: bytes, secret: str, provided: str) -> bool:
    expected = compute_signature(raw_body=raw_body, secret=secret)
    return hmac.compare_digest(expected, provided)


# ---- source creation ------------------------------------------------------

async def create_webhook_source(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    name: str,
) -> tuple[DataSource, str, str]:
    """
    Create a webhook DataSource. Returns (source, token, secret).

    The token and secret are returned in plaintext *only here*. After this
    call, the token is unrecoverable (hashed) and the secret requires the
    encryption key to recover.
    """
    token = generate_token()
    secret = generate_signing_secret()

    source = DataSource(
        tenant_id=tenant_id,
        name=name,
        source_type="webhook",
        config={},
        webhook_token_hash=hash_token(token),
        webhook_secret_encrypted=crypto.encrypt(secret),  # Fernet ciphertext
    )
    db.add(source)
    await db.flush()
    return source, token, secret


async def load_webhook_source(
    db: AsyncSession, *, token: str
) -> DataSource | None:
    token_hash = hash_token(token)
    return (
        await db.execute(
            select(DataSource).where(
                DataSource.webhook_token_hash == token_hash,
                DataSource.source_type == "webhook",
                DataSource.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()


# ---- delivery persistence -------------------------------------------------

async def record_delivery(
    db: AsyncSession,
    *,
    source: DataSource,
    raw_body: bytes,
    headers: dict[str, str],
) -> WebhookDelivery:
    delivery = WebhookDelivery(
        tenant_id=source.tenant_id,
        source_id=source.id,
        received_at=datetime.now(UTC),
        headers=dict(headers),
        raw_body=raw_body,
        status="pending",
    )
    db.add(delivery)
    await db.flush()

    # Coalesce: attach to an existing pending job if one exists for this source.
    existing = (
        await db.execute(
            select(IngestionJob).where(
                IngestionJob.source_id == source.id,
                IngestionJob.status == "pending",
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        job = IngestionJob(
            tenant_id=source.tenant_id,
            source_id=source.id,
            status="pending",
        )
        db.add(job)
        await db.flush()
        delivery.job_id = job.id
    else:
        delivery.job_id = existing.id

    await db.flush()
    from app.services.events import store as events_store
    from app.services.events import types as event_types

    await events_store.record_event(
        db,
        tenant_id=source.tenant_id,
        event_type=event_types.WEBHOOK_RECEIVED,
        source_id=source.id,
        payload={
            "delivery_id": str(delivery.id),
            "job_id": str(delivery.job_id) if delivery.job_id else None,
            "size_bytes": len(raw_body),
        },
        dedup_key=f"webhook.received:{delivery.id}",
    )
    return delivery


# ---- job processing -------------------------------------------------------

async def process_pending_deliveries(
    db: AsyncSession, *, job: IngestionJob
) -> IngestionResult:
    """
    Webhook equivalent of a connector's ingest(). Reads pending deliveries
    for this source, parses each body as one row.

    One delivery = one row. If a payload is a list, the customer should use
    the HTTP connector instead.
    """
    deliveries = (
        await db.execute(
            select(WebhookDelivery).where(
                WebhookDelivery.source_id == job.source_id,
                WebhookDelivery.status == "pending",
            ).order_by(WebhookDelivery.received_at.asc())
        )
    ).scalars().all()

    if not deliveries:
        return IngestionResult(
            rows=[],
            metadata=SourceMetadata(columns=[]),
            errors=[],
        )

    parsed: list[ParsedRow] = []
    errors: list[dict[str, Any]] = []
    for i, delivery in enumerate(deliveries, start=1):
        try:
            payload = json.loads(delivery.raw_body.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            errors.append({
                "delivery_id": str(delivery.id),
                "error": f"invalid JSON: {exc}",
            })
            delivery.status = "failed"
            continue

        if not isinstance(payload, dict):
            errors.append({
                "delivery_id": str(delivery.id),
                "error": "payload is not a JSON object",
            })
            delivery.status = "failed"
            continue

        parsed.append(ParsedRow(row_number=i, data=payload))
        delivery.status = "staged"

    # Columns = union of keys across payloads, order-preserving.
    columns: list[str] = []
    seen: set[str] = set()
    for row in parsed:
        for k in row.data.keys():
            if k not in seen:
                seen.add(k)
                columns.append(k)

    return IngestionResult(
        rows=parsed,
        metadata=SourceMetadata(columns=columns, row_count_hint=len(parsed)),
        errors=errors,
    )