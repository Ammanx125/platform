# app/db/models/agent.py
"""
On-prem agents.

An Agent is a long-lived, tenant-scoped process running on a customer's
machine. It has its own credential (bearer token, hashed at rest), its own
identity (separate from users), and its own lifecycle (enrolled by an
admin, then self-reporting).

Agents are NOT users. They don't have roles, they don't log in, they don't
carry a session. They authenticate via a bearer token that maps to one
Agent row.

An Agent is bound to exactly one DataSource. The DataSource's source_type
is 'agent'. All observations the agent reports are scoped to that source.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class Agent(Base, UUIDPrimaryKeyMixin, TimestampMixin, TenantMixin):
    """
    A registered on-prem agent.

    status:
      - 'pending'   — enrolled, not yet registered (credential not set)
      - 'active'    — registered and beating
      - 'stale'     — hasn't beaten in N minutes (set by a future worker)
      - 'revoked'   — credential invalidated; agent must re-enroll

    credential_hash: SHA-256 of the bearer token. Never the token itself.
    enrollment_token_hash: SHA-256 of the one-time enrollment token. Set
      only during the window between enroll() and register(); cleared after.
    enrollment_expires_at: the token stops working after this time.
    """
    __tablename__ = "agents"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    source_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("data_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", index=True
    )

    credential_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )
    enrollment_token_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )
    enrollment_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    enrolled_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    registered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    # Free-form: agent version, hostname, OS, allowed paths, etc. Reported
    # by the agent on register() and heartbeat(). Never trust for auth.
    agent_metadata: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )

    source = relationship("DataSource", lazy="selectin")

class AgentJob(Base, UUIDPrimaryKeyMixin, TimestampMixin, TenantMixin):
    """
    One bounded instruction for one agent.

    The registry of job types is fixed in code:
      - 'upload_file'        — params: {path, expected_hash}
      - 'rescan'             — params: {}
      - 'rotate_credential'  — params: {}
      - 'update_config'      — params: {sync_interval_seconds?, ...}

    The agent enforces its own policy (path containment, size caps, allowlist)
    before executing any job. A rejected job is reported back with
    status='rejected'.

    Status lifecycle:
      pending  → in_progress → completed | rejected | failed
      pending  → cancelled

    Jobs are single-use. Once claimed (pending → in_progress), no other
    agent iteration can claim them. If the agent dies, a future worker
    task reverts stale in_progress jobs to pending after a timeout.
    """
    __tablename__ = "agent_jobs"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    job_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", index=True
    )
    result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_agent_jobs_agent_status", "agent_id", "status"),
    )


class PendingFileUpload(Base, UUIDPrimaryKeyMixin, TimestampMixin, TenantMixin):
    """
    One file that an ingestion job is waiting for.

    Created when a customer triggers ingest on an agent source. One row per
    file. The row is delivered (or failed) when the agent uploads content
    for the file's expected content_hash.

    status:
      pending    — waiting for the agent
      delivered  — content received and stored
      failed     — agent reported it could not deliver (missing, too big,
                   changed-hash-not-resolved, retries exhausted)

    The unique key is (job_id, path). A re-triggered job gets its own rows.
    """
    __tablename__ = "pending_file_uploads"

    job_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("ingestion_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_job_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agent_jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    path: Mapped[str] = mapped_column(String(1000), nullable=False)
    expected_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    actual_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", index=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("job_id", "path", name="uq_pending_file_uploads_job_path"),
        Index("ix_pending_file_uploads_job_status", "job_id", "status"),
    )