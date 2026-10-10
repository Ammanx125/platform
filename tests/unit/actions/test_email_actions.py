# tests/unit/actions/test_email_actions.py
from __future__ import annotations

import uuid

import pytest

from app.services.actions import policy as policy_service
from app.services.actions.base import (
    RISK_ELEVATED,
    RISK_READ,
    RISK_REPORT,
    ActionContext,
)
from app.services.actions.internal.draft_email import (
    DraftEmailAction,
    DraftEmailParams,
    validate_body_not_empty,
)
from app.services.actions.internal.list_inbox import (
    ListInboxAction,
)
from app.services.actions.internal.send_email import (
    SendEmailAction,
    SendEmailParams,
    validate_draft_id_shape,
)
from app.services.actions.registry import names as tool_names

# ---------- registry ----------

def test_email_actions_registered():
    registered = tool_names()
    assert "list_inbox" in registered
    assert "draft_email" in registered
    assert "send_email" in registered


# ---------- risk levels / policy ----------

def test_risk_levels_are_correct():
    assert ListInboxAction.risk_level == RISK_READ
    assert DraftEmailAction.risk_level == RISK_REPORT
    assert SendEmailAction.risk_level == RISK_ELEVATED


def test_policy_defaults():
    # Direct checks on the mapping that policy.py already provides.
    assert policy_service._default_policy_for_risk(RISK_READ) == policy_service.POLICY_AUTO
    assert policy_service._default_policy_for_risk(RISK_REPORT) == policy_service.POLICY_AUTO
    assert (
        policy_service._default_policy_for_risk(RISK_ELEVATED)
        == policy_service.POLICY_REQUIRE_APPROVAL
    )


# ---------- validators ----------

def _ctx(evidence: list[str] | None = None) -> ActionContext:
    return ActionContext(
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        decision_run_id=None,
        evidence_ids=evidence or [],
    )


def test_draft_email_body_validator():
    ctx = _ctx()
    bad = DraftEmailParams(in_reply_to_message_id="m1", body_text="   ")
    assert not validate_body_not_empty(bad, ctx).passed
    good = DraftEmailParams(in_reply_to_message_id="m1", body_text="hello")
    assert validate_body_not_empty(good, ctx).passed


def test_send_email_draft_id_validator():
    ctx = _ctx()
    bad = SendEmailParams(draft_id="../../etc/passwd", account_id="a")
    assert not validate_draft_id_shape(bad, ctx).passed
    good = SendEmailParams(draft_id="draft-abc123", account_id="a")
    assert validate_draft_id_shape(good, ctx).passed


# ---------- execution against FakeEmailProvider ----------

@pytest.mark.asyncio
async def test_draft_email_creates_draft(two_tenants):
    """
    Full path: create a DataSource + EmailAccount with provider='fake',
    seed a Document representing the parent email, then run draft_email.
    """
    from app.db.models.dataset import DataSource
    from app.db.models.email import EmailAccount
    from app.db.models.knowledge import Document
    from app.db.session import SessionLocal
    from app.services.email.fake import FakeEmailProvider
    from app.services.email.tokens import serialize_tokens

    tenant_id = two_tenants["tenant_a"]
    user_id = two_tenants["user_a"]

    async with SessionLocal() as db:
        source = DataSource(
            tenant_id=tenant_id,
            name="Inbox",
            source_type="email_gmail",
        )
        db.add(source)
        await db.flush()

        fake = FakeEmailProvider()
        tokens = await fake.exchange_code(code="x", redirect_uri="y")
        account = EmailAccount(
            tenant_id=tenant_id,
            source_id=source.id,
            provider="fake",
            email_address=fake.email_address,
            status="active",
            oauth_tokens_encrypted=serialize_tokens(tokens),
        )
        db.add(account)

        parent = Document(
            tenant_id=tenant_id,
            source_id=source.id,
            title="URGENT: delayed delivery",
            content_type="email",
            raw_text="body",
            doc_metadata={
                "message_id": "parent-msg-1",
                "from_address": "customer@example.com",
                "subject": "URGENT: delayed delivery",
            },
            status="ready",
        )
        db.add(parent)
        await db.commit()

        action = DraftEmailAction()
        ctx = ActionContext(
            tenant_id=tenant_id,
            user_id=user_id,
            decision_run_id=None,
            evidence_ids=["doc:parent-msg-1"],
        )
        payload = DraftEmailParams(
            in_reply_to_message_id="parent-msg-1",
            body_text="We apologize for the delay and will provide a revised date.",
        )
        result = await action.execute(db=db, payload=payload, context=ctx)
        assert result.output["to_address"] == "customer@example.com"
        assert result.output["subject"].lower().startswith("re:")
        assert result.output["draft_id"].startswith("draft-")
        assert result.output["account_id"] == str(account.id)


@pytest.mark.asyncio
async def test_send_email_verifies_delivery(two_tenants):
    """Draft via fake provider, then send via SendEmailAction and verify()."""
    from app.db.models.dataset import DataSource
    from app.db.models.email import EmailAccount
    from app.db.session import SessionLocal
    from app.services.email.provider_registry import _PROVIDERS
    from app.services.email.tokens import serialize_tokens

    tenant_id = two_tenants["tenant_a"]
    user_id = two_tenants["user_a"]

    # Ensure the fake provider instance used by the action is the same one
    # the test drives, so drafts created here are visible to send_email.
    fake = _PROVIDERS["fake"]

    async with SessionLocal() as db:
        source = DataSource(
            tenant_id=tenant_id, name="Inbox", source_type="email_gmail",
        )
        db.add(source)
        await db.flush()
        tokens = await fake.exchange_code(code="x", redirect_uri="y")
        profile = await fake.get_profile(tokens=tokens)
        account = EmailAccount(
            tenant_id=tenant_id,
            source_id=source.id,
            provider="fake",
            email_address=profile["email_address"],
            status="active",
            oauth_tokens_encrypted=serialize_tokens(tokens),
        )
        db.add(account)
        await db.flush()

        # Create a draft through the provider directly — this mirrors what
        # draft_email.execute() does internally.
        draft = await fake.create_draft(
            tokens=tokens,
            to_addresses=["customer@example.com"],
            cc_addresses=[],
            subject="Re: delayed delivery",
            body_text="Apologies for the delay.",
        )
        await db.commit()

        action = SendEmailAction()
        ctx = ActionContext(
            tenant_id=tenant_id,
            user_id=user_id,
            decision_run_id=None,
            evidence_ids=["action:draft"],
        )
        payload = SendEmailParams(
            draft_id=draft.draft_id, account_id=str(account.id)
        )
        result = await action.execute(db=db, payload=payload, context=ctx)
        assert result.output["message_id"].startswith("sent-msg-")

        verification = await action.verify(
            db=db, payload=payload, context=ctx, result=result
        )
        assert verification.verified, verification.error