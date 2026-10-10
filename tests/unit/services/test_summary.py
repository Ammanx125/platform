# tests/unit/services/email/test_summary.py
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.dataset import DataSource
from app.db.models.knowledge import Document
from app.db.session import SessionLocal
from app.services.email.summary import (
    HIGH_URGENCY_LIMIT,
    MAX_WINDOW_HOURS,
    summarize_inbox,
)


def _doc(
    *, tenant_id, source_id, message_id, intent, urgency,
    from_address="a@b.example", received_at=None, subject="hello",
):
    return Document(
        tenant_id=tenant_id,
        source_id=source_id,
        title=subject,
        content_type="email",
        raw_text="body",
        doc_metadata={
            "message_id": message_id,
            "from_address": from_address,
            "subject": subject,
            "received_at": (received_at or datetime.now(UTC)).isoformat(),
            "intent": intent,
            "urgency": urgency,
        },
        status="ready",
    )


@pytest.mark.asyncio
async def test_summarize_counts_by_intent_and_urgency(two_tenants):
    tenant_id = two_tenants["tenant_a"]
    async with SessionLocal() as db:
        src = DataSource(tenant_id=tenant_id, name="Inbox", source_type="email_gmail")
        db.add(src)
        await db.flush()

        now = datetime.now(UTC)
        db.add_all([
            _doc(tenant_id=tenant_id, source_id=src.id, message_id="m1",
                 intent="complaint", urgency="urgent",
                 from_address="c1@x.example",
                 received_at=now - timedelta(minutes=10)),
            _doc(tenant_id=tenant_id, source_id=src.id, message_id="m2",
                 intent="complaint", urgency="high",
                 from_address="c1@x.example",
                 received_at=now - timedelta(hours=1)),
            _doc(tenant_id=tenant_id, source_id=src.id, message_id="m3",
                 intent="inquiry", urgency="normal",
                 from_address="c2@x.example",
                 received_at=now - timedelta(hours=2)),
            # Outside window
            _doc(tenant_id=tenant_id, source_id=src.id, message_id="m4",
                 intent="complaint", urgency="urgent",
                 from_address="c3@x.example",
                 received_at=now - timedelta(hours=48)),
        ])
        await db.commit()

        summary = await summarize_inbox(
            db, tenant_id=tenant_id, window_hours=24,
        )
        assert summary.total == 3
        assert summary.by_intent["complaint"] == 2
        assert summary.by_intent["inquiry"] == 1
        assert summary.by_urgency["urgent"] == 1
        assert summary.by_urgency["high"] == 1
        assert summary.by_urgency["normal"] == 1
        assert summary.attention_count() == 2
        assert summary.top_senders[0]["from_address"] == "c1@x.example"
        assert summary.top_senders[0]["count"] == 2
        assert len(summary.high_urgency_messages) == 2


@pytest.mark.asyncio
async def test_summarize_empty_window(two_tenants):
    tenant_id = two_tenants["tenant_a"]
    async with SessionLocal() as db:
        summary = await summarize_inbox(db, tenant_id=tenant_id, window_hours=24)
        assert summary.total == 0
        assert summary.attention_count() == 0
        assert summary.top_senders == []
        assert "No emails received" in summary.to_evidence_text()


@pytest.mark.asyncio
async def test_summarize_source_filter(two_tenants):
    tenant_id = two_tenants["tenant_a"]
    async with SessionLocal() as db:
        s1 = DataSource(tenant_id=tenant_id, name="A", source_type="email_gmail")
        s2 = DataSource(tenant_id=tenant_id, name="B", source_type="email_gmail")
        db.add_all([s1, s2])
        await db.flush()

        now = datetime.now(UTC)
        db.add_all([
            _doc(tenant_id=tenant_id, source_id=s1.id, message_id="a1",
                 intent="complaint", urgency="high", received_at=now),
            _doc(tenant_id=tenant_id, source_id=s2.id, message_id="b1",
                 intent="inquiry", urgency="normal", received_at=now),
        ])
        await db.commit()

        s1_only = await summarize_inbox(
            db, tenant_id=tenant_id, source_ids=[s1.id], window_hours=24,
        )
        assert s1_only.total == 1
        assert s1_only.by_intent["complaint"] == 1


def test_summarize_window_bounds():
    import asyncio
    import uuid as _uuid
    with pytest.raises(ValueError):
        asyncio.run(summarize_inbox(
            cast(AsyncSession, None), tenant_id=_uuid.uuid4(), window_hours=0,
        ))
    with pytest.raises(ValueError):
        asyncio.run(summarize_inbox(
            cast(AsyncSession, None), tenant_id=_uuid.uuid4(), window_hours=MAX_WINDOW_HOURS + 1,
        ))


@pytest.mark.asyncio
async def test_summarize_caps_high_urgency_messages(two_tenants):
    tenant_id = two_tenants["tenant_a"]
    async with SessionLocal() as db:
        src = DataSource(tenant_id=tenant_id, name="Inbox", source_type="email_gmail")
        db.add(src)
        await db.flush()
        now = datetime.now(UTC)
        for i in range(HIGH_URGENCY_LIMIT + 5):
            db.add(_doc(
                tenant_id=tenant_id, source_id=src.id,
                message_id=f"m{i}", intent="complaint", urgency="urgent",
                received_at=now - timedelta(minutes=i),
            ))
        await db.commit()

        summary = await summarize_inbox(db, tenant_id=tenant_id, window_hours=24)
        assert summary.attention_count() == HIGH_URGENCY_LIMIT + 5
        assert len(summary.high_urgency_messages) == HIGH_URGENCY_LIMIT