# tests/integration/test_email_summary_decision.py
from __future__ import annotations

import pytest

from app.db.session import SessionLocal
from app.services.orchestration.context import OrchestratorRequest
from app.services.orchestration.executor import execute, persist


@pytest.mark.asyncio
async def test_decision_contains_email_summary_evidence(two_tenants):
    """
    Seed emails via the fake provider, ask a complaint question, confirm
    the resulting DecisionRun carries an email_summary evidence item.
    """
    from datetime import UTC, datetime

    from app.db.models.dataset import DataSource
    from app.db.models.knowledge import Document

    tenant_id = two_tenants["tenant_a"]
    user_id = two_tenants["user_a"]

    async with SessionLocal() as db:
        src = DataSource(tenant_id=tenant_id, name="Inbox", source_type="email_gmail")
        db.add(src)
        await db.flush()
        now = datetime.now(UTC)
        for i, intent in enumerate(["complaint", "complaint", "inquiry"]):
            db.add(Document(
                tenant_id=tenant_id, source_id=src.id,
                title=f"email {i}", content_type="email", raw_text="body",
                doc_metadata={
                    "message_id": f"m{i}", "from_address": f"c{i}@x.example",
                    "subject": f"subject {i}",
                    "received_at": now.isoformat(),
                    "intent": intent,
                    "urgency": "urgent" if intent == "complaint" else "normal",
                }, status="ready",
            ))
        await db.commit()

        result = await execute(
            db,
            request=OrchestratorRequest(
                query="how many complaints did we get today?",
            ),
            tenant_id=tenant_id,
            user_id=user_id,
        )
        assert result.evidence.email_summaries, "expected email_summary evidence"
        item = result.evidence.email_summaries[0]
        assert item.data["by_intent"]["complaint"] == 2

        row = await persist(db, result=result)
        await db.commit()
        assert row.id is not None
        # Evidence persisted into the DecisionRun
        persisted = row.evidence or {}
        assert persisted.get("email_summaries"), "email_summaries missing from persisted evidence"