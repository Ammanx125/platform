# app/workers/tasks/webhooks.py
"""
Webhook task: process pending WebhookDelivery rows that have no job yet.

`record_delivery` in the webhook service already coalesces deliveries
onto a pending IngestionJob. This task's job is to ensure every delivery
has a corresponding job — the ingestion task then processes it.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.dataset import IngestionJob
from app.db.models.webhook import WebhookDelivery


async def run_webhooks_once(db: AsyncSession) -> int:
    # Find a pending delivery whose job_id is null.
    stmt = (
        select(WebhookDelivery)
        .where(
            WebhookDelivery.status == "pending",
            WebhookDelivery.job_id.is_(None),
        )
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    delivery = (await db.execute(stmt)).scalar_one_or_none()
    if delivery is None:
        return 0

    # Create a job for it.
    job = IngestionJob(
        tenant_id=delivery.tenant_id,
        source_id=delivery.source_id,
        status="pending",
    )
    db.add(job)
    await db.flush()
    delivery.job_id = job.id
    await db.flush()
    return 1