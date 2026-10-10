# app/services/actions/internal/draft_email.py
"""
Draft a reply to an existing inbox email.

Per design decision (a): the caller must cite the message being replied to
by message_id. The recipient, subject, and thread are derived from the
parent email's Document metadata — the LLM does not get to invent them.
This is the same evidence-citation discipline as generate_report.

The draft is created in the connected mailbox (Gmail draft / Graph draft)
and is NOT sent. send_email is a separate action requiring approval.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.email import EmailAccount
from app.db.models.knowledge import Document
from app.services.actions.base import (
    RISK_REPORT,
    ActionContext,
    ActionError,
    ActionResult,
    ValidationOutcome,
    VerificationOutcome,
)
from app.services.actions.registry import register
from app.services.actions.validators import validate_evidence_cited
from app.services.email.provider_registry import get_provider
from app.services.email.tokens import deserialize_tokens, is_expired, serialize_tokens


class DraftEmailParams(BaseModel):
    in_reply_to_message_id: str = Field(min_length=1, max_length=500)
    body_text: str = Field(min_length=1, max_length=50_000)


def validate_body_not_empty(
    payload: DraftEmailParams, context: ActionContext
) -> ValidationOutcome:
    if not payload.body_text.strip():
        return ValidationOutcome(
            passed=False,
            error="draft body must not be blank",
        )
    return ValidationOutcome(
        passed=True, detail={"body_length": len(payload.body_text)}
    )


class DraftEmailAction:
    name = "draft_email"
    description = (
        "Create a draft reply to an existing inbox email. Requires the "
        "message_id of the email being replied to (from list_inbox or "
        "retrieval evidence). Does NOT send — sends require approval."
    )
    risk_level = RISK_REPORT
    parameters_model = DraftEmailParams
    validators = [validate_evidence_cited, validate_body_not_empty]

    async def execute(
        self,
        *,
        db: Any,
        payload: DraftEmailParams,
        context: ActionContext,
    ) -> ActionResult:
        session: AsyncSession = db

        parent = (
            await session.execute(
                select(Document).where(
                    Document.tenant_id == context.tenant_id,
                    Document.content_type == "email",
                    Document.doc_metadata["message_id"].astext
                    == payload.in_reply_to_message_id,
                ).limit(1)
            )
        ).scalar_one_or_none()
        if parent is None:
            raise ActionError(
                f"no email found with message_id={payload.in_reply_to_message_id!r}"
            )

        meta = parent.doc_metadata or {}
        to_address = meta.get("from_address")
        if not to_address:
            raise ActionError("parent email has no from_address")

        parent_subject = meta.get("subject") or ""
        reply_subject = (
            parent_subject
            if parent_subject.lower().startswith("re:")
            else f"Re: {parent_subject}".strip()
        )

        account = (
            await session.execute(
                select(EmailAccount).where(
                    EmailAccount.tenant_id == context.tenant_id,
                    EmailAccount.source_id == parent.source_id,
                )
            )
        ).scalar_one_or_none()
        if account is None:
            raise ActionError("no email account for the parent email's source")
        if account.status == "revoked":
            raise ActionError("email account has been revoked")

        provider = get_provider(account.provider)
        tokens = deserialize_tokens(account.oauth_tokens_encrypted)
        if is_expired(tokens):
            tokens = await provider.refresh_tokens(tokens=tokens)
            account.oauth_tokens_encrypted = serialize_tokens(tokens)
            await session.flush()

        draft = await provider.create_draft(
            tokens=tokens,
            to_addresses=[to_address],
            cc_addresses=[],
            subject=reply_subject,
            body_text=payload.body_text,
            in_reply_to_message_id=payload.in_reply_to_message_id,
        )

        return ActionResult(output={
            "draft_id": draft.draft_id,
            "draft_message_id": draft.message_id,
            "thread_id": draft.thread_id,
            "to_address": to_address,
            "subject": reply_subject,
            "body_text": payload.body_text,
            "in_reply_to_message_id": payload.in_reply_to_message_id,
            "provider": account.provider,
            "account_id": str(account.id),
        })

    async def verify(
        self,
        *,
        db: Any,
        payload: DraftEmailParams,
        context: ActionContext,
        result: ActionResult,
    ) -> VerificationOutcome:
        session: AsyncSession = db
        draft_id = result.output.get("draft_id")
        account_id = result.output.get("account_id")
        if not draft_id or not account_id:
            return VerificationOutcome(
                verified=False,
                error="missing draft_id or account_id in result",
            )

        account = (
            await session.execute(
                select(EmailAccount).where(
                    EmailAccount.id == account_id,
                    EmailAccount.tenant_id == context.tenant_id,
                )
            )
        ).scalar_one_or_none()
        if account is None:
            return VerificationOutcome(
                verified=False,
                error="email account disappeared after draft creation",
            )

        provider = get_provider(account.provider)
        tokens = deserialize_tokens(account.oauth_tokens_encrypted)
        if is_expired(tokens):
            tokens = await provider.refresh_tokens(tokens=tokens)

        fetched = await provider.get_draft(tokens=tokens, draft_id=draft_id)
        if fetched is None:
            return VerificationOutcome(
                verified=False,
                error=f"draft {draft_id} not found in provider",
                detail={"draft_id": draft_id},
            )
        return VerificationOutcome(
            verified=True,
            detail={"draft_id": draft_id, "provider": account.provider},
        )


register(DraftEmailAction())