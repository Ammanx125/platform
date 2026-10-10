# app/services/actions/internal/list_inbox.py
"""
List inbox emails, filtered by intent / urgency / time window.

Read-only. Returned results are Documents with content_type='email'. The
result payload is deliberately small: id, subject, from, received_at,
intent, urgency. The full body lives in the Document and is retrieved
through normal retrieval when the model asks for it.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.knowledge import Document
from app.services.actions.base import (
    RISK_READ,
    ActionContext,
    ActionResult,
    VerificationOutcome,
)
from app.services.actions.registry import register


class ListInboxParams(BaseModel):
    intent: Literal[
        "complaint", "inquiry", "order", "invoice",
        "support", "feedback", "internal", "unknown",
    ] | None = Field(default=None)
    urgency: Literal["low", "normal", "high", "urgent"] | None = Field(default=None)
    days: int = Field(default=7, ge=1, le=90)
    limit: int = Field(default=20, ge=1, le=100)


class ListInboxAction:
    name = "list_inbox"
    description = (
        "List recent inbox emails. Optionally filter by intent (complaint, "
        "inquiry, order, invoice, support, feedback, internal) or urgency "
        "(low, normal, high, urgent). Returns summary rows, not full bodies."
    )
    risk_level = RISK_READ
    parameters_model = ListInboxParams
    validators: list = []

    async def execute(
        self,
        *,
        db: Any,
        payload: ListInboxParams,
        context: ActionContext,
    ) -> ActionResult:
        session: AsyncSession = db
        cutoff = datetime.now(UTC) - timedelta(days=payload.days)

        stmt = (
            select(Document)
            .where(
                Document.tenant_id == context.tenant_id,
                Document.content_type == "email",
                Document.status == "ready",
            )
            .order_by(Document.created_at.desc())
            .limit(payload.limit * 3)  # over-fetch, filter in Python on JSONB
        )
        if context.source_ids:
            stmt = stmt.where(Document.source_id.in_(context.source_ids))

        docs = list((await session.execute(stmt)).scalars().all())

        rows: list[dict] = []
        for doc in docs:
            meta = doc.doc_metadata or {}
            received_at_raw = meta.get("received_at")
            if not received_at_raw:
                continue
            try:
                received_at = datetime.fromisoformat(received_at_raw)
            except ValueError:
                continue
            if received_at.tzinfo is None:
                received_at = received_at.replace(tzinfo=UTC)
            if received_at < cutoff:
                continue
            if payload.intent and meta.get("intent") != payload.intent:
                continue
            if payload.urgency and meta.get("urgency") != payload.urgency:
                continue

            rows.append({
                "document_id": str(doc.id),
                "message_id": meta.get("message_id"),
                "subject": meta.get("subject") or doc.title,
                "from_address": meta.get("from_address"),
                "from_name": meta.get("from_name"),
                "received_at": received_at.isoformat(),
                "intent": meta.get("intent"),
                "urgency": meta.get("urgency"),
            })
            if len(rows) >= payload.limit:
                break

        return ActionResult(output={
            "count": len(rows),
            "days": payload.days,
            "intent_filter": payload.intent,
            "urgency_filter": payload.urgency,
            "emails": rows,
        })

    async def verify(
        self,
        *,
        db: Any,
        payload: ListInboxParams,
        context: ActionContext,
        result: ActionResult,
    ) -> VerificationOutcome:
        # Read-only action; nothing to verify beyond execute() not raising.
        return VerificationOutcome(
            verified=True,
            detail={"count": result.output.get("count", 0)},
        )


register(ListInboxAction())