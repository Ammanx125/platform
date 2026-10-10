# app/services/actions/internal/send_email.py
"""
Send a previously-created draft.

Risk level is ELEVATED, which the default policy maps to require_approval.
The LLM must therefore go through the human approval queue to reach this
action; there is no path that lets it autonomously send business email.

The design contract (from the mentor's guidance):
    draft → approve → send, never draft → send.

This action sends a draft by id. It does not accept recipient or body
arguments; the draft is the source of truth. That way the message the
approver saw is exactly the message that ships.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.email import EmailAccount
from app.services.actions.base import (
    RISK_ELEVATED,
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


class SendEmailParams(BaseModel):
    draft_id: str = Field(min_length=1, max_length=500)
    account_id: str = Field(min_length=1, max_length=64)


def validate_draft_id_shape(
    payload: SendEmailParams, context: ActionContext
) -> ValidationOutcome:
    if "/" in payload.draft_id or ".." in payload.draft_id:
        return ValidationOutcome(
            passed=False,
            error="draft_id contains illegal characters",
        )
    return ValidationOutcome(passed=True)


class SendEmailAction:
    name = "send_email"
    description = (
        "Send a previously-created email draft. Requires approval. "
        "Provide draft_id and account_id from draft_email's output."
    )
    risk_level = RISK_ELEVATED
    parameters_model = SendEmailParams
    validators = [validate_evidence_cited, validate_draft_id_shape]

    async def execute(
        self,
        *,
        db: Any,
        payload: SendEmailParams,
        context: ActionContext,
    ) -> ActionResult:
        session: AsyncSession = db

        account = (
            await session.execute(
                select(EmailAccount).where(
                    EmailAccount.id == payload.account_id,
                    EmailAccount.tenant_id == context.tenant_id,
                )
            )
        ).scalar_one_or_none()
        if account is None:
            raise ActionError("email account not found for tenant")
        if account.status == "revoked":
            raise ActionError("email account has been revoked")

        provider = get_provider(account.provider)
        tokens = deserialize_tokens(account.oauth_tokens_encrypted)
        if is_expired(tokens):
            tokens = await provider.refresh_tokens(tokens=tokens)
            account.oauth_tokens_encrypted = serialize_tokens(tokens)
            await session.flush()

        # Confirm the draft exists before sending. If it doesn't, this is
        # not an ActionError to retry — the draft was never real.
        draft = await provider.get_draft(tokens=tokens, draft_id=payload.draft_id)
        if draft is None:
            raise ActionError(f"draft {payload.draft_id!r} not found in provider")

        sent = await provider.send_draft(tokens=tokens, draft_id=payload.draft_id)

        return ActionResult(output={
            "message_id": sent.message_id,
            "thread_id": sent.thread_id,
            "draft_id": payload.draft_id,
            "account_id": payload.account_id,
            "provider": account.provider,
        })

    async def verify(
        self,
        *,
        db: Any,
        payload: SendEmailParams,
        context: ActionContext,
        result: ActionResult,
    ) -> VerificationOutcome:
        session: AsyncSession = db
        message_id = result.output.get("message_id")
        if not message_id:
            return VerificationOutcome(
                verified=False,
                error="no message_id in send result",
            )

        account = (
            await session.execute(
                select(EmailAccount).where(
                    EmailAccount.id == payload.account_id,
                    EmailAccount.tenant_id == context.tenant_id,
                )
            )
        ).scalar_one_or_none()
        if account is None:
            return VerificationOutcome(
                verified=False, error="email account missing after send"
            )

        provider = get_provider(account.provider)
        tokens = deserialize_tokens(account.oauth_tokens_encrypted)
        if is_expired(tokens):
            tokens = await provider.refresh_tokens(tokens=tokens)

        sent = await provider.get_sent_message(
            tokens=tokens, message_id=message_id
        )
        if sent is None:
            return VerificationOutcome(
                verified=False,
                error=f"sent message {message_id} not found in provider",
                detail={"message_id": message_id},
            )

        # Confirm the message is in the sent folder, not just visible
        # somewhere (a draft is also "visible"; we want proof of send).
        sent_marker = {
            "gmail": "SENT",
            "graph": "sentitems",
            "fake": "SENT",
        }.get(account.provider)
        if sent_marker is None:
            return VerificationOutcome(
                verified=False,
                error=f"no sent-folder marker configured for provider {account.provider!r}",
                detail={"provider": account.provider},
            )
        if sent_marker not in sent.labels:
            return VerificationOutcome(
                verified=False,
                error=f"message {message_id} exists but is not marked sent",
                detail={"labels": sent.labels, "expected_marker": sent_marker},
            )

        return VerificationOutcome(
            verified=True,
            detail={
                "message_id": message_id,
                "thread_id": sent.thread_id,
                "provider": account.provider,
            },
        )


register(SendEmailAction())