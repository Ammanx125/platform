# app/services/email/base.py
"""
Email provider abstraction.

An EmailProvider is a concrete adapter for one mail backend (Gmail,
Microsoft Graph, ...). It is responsible for exactly four things:

  - OAuth:  exchange an authorization code for tokens, refresh expired
            tokens, revoke them.
  - Read:   list message ids newer than a cursor, fetch one message body.
  - Draft:  create a draft in the mailbox (does NOT send).
  - Send:   send an existing draft, or send a raw composed message.

Everything else (classification, ingestion, storage, policy, approval) is
Sansa's responsibility, not the provider's.

Why this interface and not the raw Google/Microsoft SDK signatures:
the orchestration and ingestion layers must be provider-agnostic. Adding
Microsoft Graph later should mean writing one new module that implements
this Protocol, and changing zero call sites.

FetchedEmail is deliberately minimal. Anything the classifier needs is
here; anything that lives only in provider-specific metadata stays in
`raw_headers` as a passthrough dict, so we don't lose it, but we never
program against it.

Tokens:
    OAuthTokenSet is the plaintext shape that gets Fernet-encrypted
    into EmailAccount.oauth_tokens_encrypted. Never persist it raw.
    Never log it. Never include it in ActionResult.output.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


class EmailProviderError(Exception):
    """Raised when a provider operation fails unrecoverably."""


class EmailAuthError(EmailProviderError):
    """Raised when OAuth tokens are invalid, expired, or revoked."""


@dataclass(frozen=True)
class OAuthTokenSet:
    """
    Plaintext OAuth token bundle. Encrypted at rest as JSON.

    expires_at is timezone-aware UTC. Google returns `expires_in` (seconds
    from now); the provider converts it to an absolute time.
    """
    access_token: str
    refresh_token: str | None
    expires_at: datetime
    scope: str
    token_type: str = "Bearer"


@dataclass(frozen=True)
class FetchedEmail:
    """
    One message as read from a provider, before classification.

    message_id:    provider-stable id (Gmail: message id, Graph: message id)
    thread_id:     provider thread/conversation id
    from_address:  normalized lowercased email address
    from_name:     display name if available, else None
    to_addresses:  list of normalized recipient addresses
    cc_addresses:  list of normalized cc addresses
    subject:       decoded subject line (empty string if none)
    body_text:     plain-text body. If the provider only gives HTML,
                   the adapter must strip it to text before returning.
    received_at:   timezone-aware UTC timestamp of when the message
                   arrived at the mailbox
    snippet:       provider-supplied short preview (Gmail: snippet field)
    labels:        provider labels/categories (Gmail: labelIds)
    raw_headers:   passthrough of anything else the provider gives us,
                   for future use. Never load-bearing.
    """
    message_id: str
    thread_id: str
    from_address: str
    from_name: str | None
    to_addresses: list[str]
    cc_addresses: list[str]
    subject: str
    body_text: str
    received_at: datetime
    snippet: str
    labels: list[str] = field(default_factory=list)
    raw_headers: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DraftResult:
    """
    The provider's response to a create_draft() call.
    """
    draft_id: str
    message_id: str
    thread_id: str


@dataclass(frozen=True)
class SendResult:
    """
    The provider's response to a send() call.

    message_id and thread_id are the provider's authoritative ids for the
    sent message. verify() re-fetches by message_id to confirm delivery.
    """
    message_id: str
    thread_id: str


class EmailProvider(Protocol):
    """
    Concrete adapters implement all of these. No method may assume a DB
    session; the provider is a pure I/O boundary against the mail service.
    """
    name: str  # "gmail" | "graph"

    async def build_authorize_url(
        self,
        *,
        state: str,
        redirect_uri: str,
    ) -> str: ...

    async def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
    ) -> OAuthTokenSet: ...

    async def refresh_tokens(
        self,
        *,
        tokens: OAuthTokenSet,
    ) -> OAuthTokenSet: ...

    async def revoke_tokens(self, *, tokens: OAuthTokenSet) -> None: ...

    async def get_profile(
        self, *, tokens: OAuthTokenSet
    ) -> dict[str, Any]:
        """
        Return at least {"email_address": ..., "display_name": ...}.
        Used once at OAuth-callback time to fill EmailAccount fields.
        """
        ...

    async def list_message_ids(
        self,
        *,
        tokens: OAuthTokenSet,
        cursor: str | None,
        max_messages: int,
    ) -> tuple[list[str], str | None]:
        """
        Return (message_ids, next_cursor). If cursor is None, this is the
        first sync: return the most recent messages. Otherwise, return
        messages newer than the cursor. The caller does not interpret the
        cursor; it stores it opaque.
        """
        ...

    async def get_message(
        self,
        *,
        tokens: OAuthTokenSet,
        message_id: str,
    ) -> FetchedEmail: ...

    async def get_sent_message(
        self,
        *,
        tokens: OAuthTokenSet,
        message_id: str,
    ) -> FetchedEmail | None:
        """
        Fetch a sent message by id, or return None if it doesn't exist.

        Used by send_email.verify() to confirm the send actually happened.
        A returned FetchedEmail whose labels include the provider's
        sent-folder marker (Gmail: 'SENT', Graph: 'sentitems') is the
        verification signal.
        """
        ...

    async def create_draft(
        self,
        *,
        tokens: OAuthTokenSet,
        to_addresses: list[str],
        cc_addresses: list[str],
        subject: str,
        body_text: str,
        in_reply_to_message_id: str | None = None,
    ) -> DraftResult: ...

    async def send_draft(
        self,
        *,
        tokens: OAuthTokenSet,
        draft_id: str,
    ) -> SendResult: ...

    async def get_draft(
        self,
        *,
        tokens: OAuthTokenSet,
        draft_id: str,
    ) -> DraftResult | None:
        """
        Fetch a draft by id, or None if it doesn't exist.

        Used by draft_email.verify() to confirm the draft persisted.
        """
        ...