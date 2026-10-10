# app/db/models/email.py
"""
Email accounts connected to Sansa.

An EmailAccount is a tenant-scoped OAuth connection to a mailbox
(Gmail / Microsoft Graph). It is structurally parallel to Agent:

  - both attach to exactly one DataSource
  - both carry their own lifecycle (pending -> active -> revoked)
  - both hold recoverable credentials encrypted at rest

But they are NOT the same thing. An Agent is a long-lived on-prem process.
An EmailAccount is a cloud OAuth grant; nothing runs on the customer's
machine. Keeping them separate keeps DataSource.config clean and mirrors
the existing Agent pattern (FK to data_sources.id).

The DataSource.source_type for these rows is 'email_gmail' or
'email_graph'. All synced messages become Document + StagedRow rows
scoped to that DataSource.

Tokens are Fernet-encrypted with app.core.crypto.encrypt(). The plaintext
is a JSON blob:
    {
        "access_token": "...",
        "refresh_token": "...",
        "expires_at": "2026-10-04T12:34:56+00:00",
        "scope": "https://www.googleapis.com/auth/gmail.readonly ...",
        "token_type": "Bearer"
    }
Never store raw tokens in DataSource.config, logs, or audit rows.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class EmailAccount(Base, UUIDPrimaryKeyMixin, TimestampMixin, TenantMixin):
    """
    status:
      - 'pending'  - created, OAuth not yet completed
      - 'active'   - OAuth completed, syncing
      - 'error'    - last sync or token refresh failed (see last_error)
      - 'revoked'  - user revoked access or admin disconnected

    provider:
      - 'gmail'  - Google Workspace / Gmail, source_type='email_gmail'
      - 'graph'  - Microsoft 365 / Outlook, source_type='email_graph'

    sync_cursor:
      - Gmail:   historyId (string)
      - Graph:   deltaLink (opaque URL)
      Opaque to us. The connector interprets it.

    account_metadata:
      Free-form per-provider data: granted scopes, watch expiration,
      subscription id, sync stats. Never secrets.
    """
    __tablename__ = "email_accounts"

    source_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("data_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    provider: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    email_address: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", index=True
    )

    # Fernet-encrypted JSON blob. See module docstring for shape.
    oauth_tokens_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    sync_cursor: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sync_cursor_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    connected_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    account_metadata: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )

    source = relationship("DataSource", lazy="selectin")

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "email_address",
            name="uq_email_accounts_tenant_address",
        ),
        Index("ix_email_accounts_tenant_status", "tenant_id", "status"),
    )

class EmailOAuthState(Base, UUIDPrimaryKeyMixin, TimestampMixin, TenantMixin):
    """
    One pending OAuth authorization.

    Created by POST /email/connect (authenticated), consumed once by
    GET /email/oauth/callback (unauthenticated — the caller is Google's
    redirect, not a Sansa session).

    Why a table and not a signed cookie:
      - The callback is unauthenticated. We need an authoritative,
        server-side record that binds this authorization to a tenant and
        a user, or an attacker could trick a victim into linking the
        attacker's mailbox.
      - One-use enforcement is trivial and race-free (SELECT FOR UPDATE
        + set consumed_at).
      - TTL is enforced on read; expired rows are simply rejected and
        can be swept by a future worker.

    state_hash:
        SHA-256 of the random state nonce handed to the OAuth provider.
        We never store the plaintext nonce. Lookups hash the incoming
        nonce and match on the hash, like Agent.enrollment_token_hash.

    consumed_at:
        Set once the callback succeeds. A second callback with the same
        state finds a consumed row and is rejected.

    status:
        'pending'  - created, waiting for callback
        'consumed' - callback completed
        'expired'  - past expires_at, never completed
        'failed'   - callback ran but token exchange failed
    """
    __tablename__ = "email_oauth_states"

    source_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("data_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    provider: Mapped[str] = mapped_column(String(20), nullable=False)

    # SHA-256 of the nonce; plaintext never stored.
    state_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )

    redirect_uri: Mapped[str] = mapped_column(String(500), nullable=False)
    scopes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", index=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)