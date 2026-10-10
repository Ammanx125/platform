# app/services/email/fake.py
"""
In-memory EmailProvider for tests and local development.

Implements the exact same Protocol as GmailProvider. It is what the
integration tests use to drive the whole email ingestion pipeline without
touching the network, and it is what `make dev` uses when
SANSA_EMAIL_PROVIDER=fake.

Data is deliberately shaped to reproduce the demo story:
  - 17 emails in a first batch
  - 5 of them are high-urgency complaints about delayed delivery
  - the rest spread across inquiry / order / invoice / feedback / internal

Deterministic. Same instance → same messages → same classifications.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.services.email.base import (
    DraftResult,
    EmailAuthError,
    EmailProviderError,
    FetchedEmail,
    OAuthTokenSet,
    SendResult,
)

# Fixed "today" anchor so tests can predict dates. In production this is
# never used; the fake is only active in tests/dev.
_FAKE_NOW = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)


# (subject, body, from_address, urgency, intent_hint)
# intent_hint is unused by the classifier — it's documentation for whoever
# reads the fixture about what classification we expect.
_SEED_MESSAGES: list[dict[str, Any]] = [
    {
        "from": "abebe.k@northwind.example",
        "subject": "URGENT: delayed delivery of order #4421",
        "body": (
            "We still have not received the shipment scheduled for last "
            "Wednesday. This is unacceptable and it is now affecting our "
            "own customers. Please confirm a revised delivery date today."
        ),
        "intent_hint": "complaint",
        "urgency_hint": "urgent",
    },
    {
        "from": "sara.m@atlas-logistics.example",
        "subject": "Complaint: wrong items delivered",
        "body": (
            "The consignment arrived this morning but contains the wrong "
            "items. We are very disappointed. Please arrange a refund or "
            "replacement immediately."
        ),
        "intent_hint": "complaint",
        "urgency_hint": "urgent",
    },
    {
        "from": "daniel.t@coastal.example",
        "subject": "Still waiting on invoice #INV-9031",
        "body": (
            "This is our third request. The invoice was due last week and "
            "we have received no response. Please follow up today."
        ),
        "intent_hint": "complaint",
        "urgency_hint": "urgent",
    },
    {
        "from": "hana.g@meridian-customer.example",
        "subject": "Damaged goods, second request",
        "body": (
            "The replacement parts arrived damaged. This is the second "
            "time. We need this resolved urgently."
        ),
        "intent_hint": "complaint",
        "urgency_hint": "urgent",
    },
    {
        "from": "operations@blueharbor.example",
        "subject": "URGENT - missing shipment, no response",
        "body": (
            "We have escalated internally. This delayed shipment is now "
            "critical for our operations. Please respond immediately."
        ),
        "intent_hint": "complaint",
        "urgency_hint": "urgent",
    },
    {
        "from": "procurement@redline.example",
        "subject": "Request for quotation - Q1 supply",
        "body": (
            "We would like a quote for the following items. Please provide "
            "your best pricing and lead times."
        ),
        "intent_hint": "order",
        "urgency_hint": "normal",
    },
    {
        "from": "accounts@silverline.example",
        "subject": "Payment sent for invoice #INV-8871",
        "body": (
            "Please find attached confirmation of payment sent today. "
            "Kindly update your records."
        ),
        "intent_hint": "invoice",
        "urgency_hint": "normal",
    },
    {
        "from": "hello@eastbridge.example",
        "subject": "Information about your enterprise plan",
        "body": (
            "We are evaluating providers and would like to know more "
            "details about your enterprise plan, including SLAs."
        ),
        "intent_hint": "inquiry",
        "urgency_hint": "normal",
    },
    {
        "from": "support@westgate.example",
        "subject": "Cannot access the customer portal",
        "body": (
            "Several of our users are unable to log in to the portal. We "
            "have tried resetting passwords without success."
        ),
        "intent_hint": "support",
        "urgency_hint": "high",
    },
    {
        "from": "feedback@harborline.example",
        "subject": "Thank you for the fast turnaround",
        "body": (
            "Just wanted to say the team did a great job on the last order. "
            "We really appreciate it."
        ),
        "intent_hint": "feedback",
        "urgency_hint": "low",
    },
    {
        "from": "orders@northport.example",
        "subject": "New order - please confirm",
        "body": (
            "Please place an order for 40 units of SKU-220. Confirm by "
            "end of day so we can schedule delivery."
        ),
        "intent_hint": "order",
        "urgency_hint": "high",
    },
    {
        "from": "billing@crescent.example",
        "subject": "Outstanding balance on account",
        "body": (
            "Our records show an outstanding balance. Please confirm the "
            "amount due and the due date."
        ),
        "intent_hint": "invoice",
        "urgency_hint": "normal",
    },
    {
        "from": "questions@meridian-customer.example",
        "subject": "Question about delivery windows",
        "body": (
            "Could you tell us whether delivery windows can be narrowed "
            "to a specific half-day?"
        ),
        "intent_hint": "inquiry",
        "urgency_hint": "normal",
    },
    {
        "from": "issues@lakeside.example",
        "subject": "Problem with recent integration",
        "body": (
            "The integration stopped working yesterday. We need help to "
            "diagnose the error."
        ),
        "intent_hint": "support",
        "urgency_hint": "high",
    },
    {
        "from": "suggestions@uplands.example",
        "subject": "Suggestion for reporting dashboard",
        "body": (
            "Would be nice if the dashboard could export to PDF. No rush, "
            "just a suggestion."
        ),
        "intent_hint": "feedback",
        "urgency_hint": "low",
    },
    {
        "from": "purchasing@riverbend.example",
        "subject": "Re: supply contract renewal",
        "body": (
            "Following up on our supply contract. Could you confirm the "
            "renewal terms we discussed?"
        ),
        "intent_hint": "inquiry",
        "urgency_hint": "high",
    },
    {
        "from": "reception@meridian.example",
        "subject": "Internal: quarterly planning",
        "body": (
            "Reminder about the quarterly planning session tomorrow. "
            "Please review the attached agenda."
        ),
        "intent_hint": "internal",
        "urgency_hint": "normal",
    },
]


class FakeEmailProvider:
    """
    Deterministic, in-memory EmailProvider.

    Supports the exact Protocol surface. Does not persist between process
    restarts; state lives on the instance. If you need cross-process state
    for a dev demo, wrap this in the DB-backed fake — but for tests, the
    instance-local state is exactly what we want.
    """
    name = "fake"

    def __init__(
        self,
        *,
        email_address: str = "manager@meridian.example",
        display_name: str = "Meridian Manager",
    ) -> None:
        self.email_address = email_address
        self.display_name = display_name
        self._message_ids: list[str] = [f"fake-msg-{i:03d}" for i in range(len(_SEED_MESSAGES))]
        self._drafts: dict[str, dict[str, Any]] = {}
        self._sent: dict[str, SendResult] = {}

    async def build_authorize_url(self, *, state: str, redirect_uri: str) -> str:
        return f"https://fake.example/oauth?state={state}&redirect_uri={redirect_uri}"

    async def exchange_code(self, *, code: str, redirect_uri: str) -> OAuthTokenSet:
        if not code:
            raise EmailAuthError("empty authorization code")
        return OAuthTokenSet(
            access_token="fake-access-token",
            refresh_token="fake-refresh-token",
            expires_at=_FAKE_NOW + timedelta(hours=1),
            scope="fake.scope",
        )

    async def refresh_tokens(self, *, tokens: OAuthTokenSet) -> OAuthTokenSet:
        return OAuthTokenSet(
            access_token="fake-access-token-refreshed",
            refresh_token=tokens.refresh_token,
            expires_at=_FAKE_NOW + timedelta(hours=1),
            scope=tokens.scope,
            token_type=tokens.token_type,
        )

    async def revoke_tokens(self, *, tokens: OAuthTokenSet) -> None:
        return None

    async def get_profile(self, *, tokens: OAuthTokenSet) -> dict[str, Any]:
        return {
            "email_address": self.email_address,
            "display_name": self.display_name,
        }

    async def list_message_ids(
        self,
        *,
        tokens: OAuthTokenSet,
        cursor: str | None,
        max_messages: int,
    ) -> tuple[list[str], str | None]:
        # First sync: return all messages, in reverse-chronological order,
        # capped at max_messages. Cursor is the count as a string.
        start = int(cursor) if cursor else 0
        window = self._message_ids[start:start + max_messages]
        next_cursor = str(start + len(window))
        if start + len(window) >= len(self._message_ids):
            next_cursor = str(len(self._message_ids))
        return window, next_cursor

    async def get_message(
        self, *, tokens: OAuthTokenSet, message_id: str
    ) -> FetchedEmail:
        try:
            idx = self._message_ids.index(message_id)
        except ValueError as exc:
            raise EmailProviderError(f"unknown message_id: {message_id}") from exc

        seed = _SEED_MESSAGES[idx]
        # Stagger received_at so ordering is deterministic and testable.
        received_at = _FAKE_NOW - timedelta(minutes=idx * 10)

        return FetchedEmail(
            message_id=message_id,
            thread_id=f"thread-{idx:03d}",
            from_address=seed["from"].lower(),
            from_name=seed["from"].split("@")[0].replace(".", " ").title(),
            to_addresses=[self.email_address],
            cc_addresses=[],
            subject=seed["subject"],
            body_text=seed["body"],
            received_at=received_at,
            snippet=seed["body"][:140],
            labels=["INBOX"],
            raw_headers={"intent_hint": seed["intent_hint"], "urgency_hint": seed["urgency_hint"]},
        )

    async def create_draft(
        self,
        *,
        tokens: OAuthTokenSet,
        to_addresses: list[str],
        cc_addresses: list[str],
        subject: str,
        body_text: str,
        in_reply_to_message_id: str | None = None,
    ) -> DraftResult:
        draft_id = f"draft-{uuid.uuid4().hex[:12]}"
        self._drafts[draft_id] = {
            "to_addresses": to_addresses,
            "cc_addresses": cc_addresses,
            "subject": subject,
            "body_text": body_text,
            "in_reply_to_message_id": in_reply_to_message_id,
        }
        return DraftResult(
            draft_id=draft_id,
            message_id=f"draft-msg-{draft_id}",
            thread_id=in_reply_to_message_id or f"thread-{draft_id}",
        )

    async def send_draft(
        self, *, tokens: OAuthTokenSet, draft_id: str
    ) -> SendResult:
        if draft_id not in self._drafts:
            raise EmailProviderError(f"unknown draft_id: {draft_id}")
        result = SendResult(
            message_id=f"sent-msg-{draft_id}",
            thread_id=f"thread-{draft_id}",
        )
        self._sent[draft_id] = result
        return result

    async def get_draft(
        self, *, tokens: OAuthTokenSet, draft_id: str
    ) -> DraftResult | None:
        if draft_id not in self._drafts:
            return None
        d = self._drafts[draft_id]
        return DraftResult(
            draft_id=draft_id,
            message_id=f"draft-msg-{draft_id}",
            thread_id=d.get("in_reply_to_message_id") or f"thread-{draft_id}",
        )

    async def get_sent_message(
        self, *, tokens: OAuthTokenSet, message_id: str
    ) -> FetchedEmail | None:
        # Sent messages are tracked under send_draft; find one that matches.
        for draft_id, result in self._sent.items():
            if result.message_id == message_id:
                d = self._drafts.get(draft_id, {})
                return FetchedEmail(
                    message_id=result.message_id,
                    thread_id=result.thread_id,
                    from_address=self.email_address,
                    from_name=self.display_name,
                    to_addresses=list(d.get("to_addresses", [])),
                    cc_addresses=list(d.get("cc_addresses", [])),
                    subject=str(d.get("subject", "")),
                    body_text=str(d.get("body_text", "")),
                    received_at=_FAKE_NOW,
                    snippet=str(d.get("body_text", ""))[:140],
                    labels=["SENT"],
                )
        return None