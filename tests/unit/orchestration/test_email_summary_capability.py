# tests/unit/orchestration/test_email_summary_capability.py
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.db.models.dataset import DataSource
from app.db.models.knowledge import Document
from app.db.session import SessionLocal
from app.services.orchestration.capabilities import get_capability


@pytest.mark.asyncio
async def test_email_summary_capability_returns_one_item(two_tenants):
    tenant_id = two_tenants["tenant_a"]
    async with SessionLocal() as db:
        src = DataSource(tenant_id=tenant_id, name="Inbox", source_type="email_gmail")
        db.add(src)
        await db.flush()
        now = datetime.now(UTC)
        db.add(Document(
            tenant_id=tenant_id, source_id=src.id, title="urgent",
            content_type="email", raw_text="b",
            doc_metadata={
                "message_id": "m1", "from_address": "x@y.example",
                "subject": "URGENT", "received_at": now.isoformat(),
                "intent": "complaint", "urgency": "urgent",
            }, status="ready",
        ))
        await db.commit()

        cap = get_capability("email_summary")
        items = await cap.run(
            db=db, tenant_id=tenant_id, step_params={"window_hours": 24},
            query="how many complaints today?", source_ids=None,
        )
        assert len(items) == 1
        item = items[0]
        assert item.kind == "email_summary"
        assert item.data["total"] == 1
        assert item.data["by_intent"]["complaint"] == 1
        # Sender-controlled fields are wrapped as untrusted.
        assert item.data["high_urgency_messages"][0]["subject"].startswith(
            "[UNTRUSTED CONTENT"
        ) or "UNTRUSTED" in item.data["high_urgency_messages"][0]["subject"]


@pytest.mark.asyncio
async def test_email_summary_capability_zero_match(two_tenants):
    tenant_id = two_tenants["tenant_a"]
    async with SessionLocal() as db:
        cap = get_capability("email_summary")
        items = await cap.run(
            db=db, tenant_id=tenant_id, step_params={},
            query="any emails?", source_ids=None,
        )
        assert len(items) == 1
        assert items[0].data["total"] == 0
        assert "No emails received" in items[0].text