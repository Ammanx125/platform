# app/services/agents/service.py
"""
Agent lifecycle: enrollment, registration, authentication, heartbeat,
and file observation ingestion.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.agent import Agent
from app.db.models.dataset import DataSource
from app.services.audit import service as audit_service
from app.services.timestamps.files import observe_file


class AgentError(Exception):
    """Agent operation failed."""


# Enrollment tokens live for this long between enroll() and register().
ENROLLMENT_TTL_MINUTES = 30

# Bearer tokens are long-lived. Revocation is manual (status='revoked').
CREDENTIAL_BYTES = 48


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def create_agent_enrollment(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    name: str,
    description: str | None,
    enrolled_by_user_id: uuid.UUID,
) -> tuple[Agent, str]:
    """
    Admin action. Creates an Agent row in 'pending' state and returns the
    one-time enrollment token. The token is shown once and never again.
    """
    # Verify the source belongs to the tenant and is of type 'agent'.
    source = (
        await db.execute(
            select(DataSource).where(
                DataSource.id == source_id,
                DataSource.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if source is None:
        raise AgentError("source not found")
    if source.source_type != "agent":
        raise AgentError(
            f"source type must be 'agent', got {source.source_type!r}"
        )

    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(minutes=ENROLLMENT_TTL_MINUTES)

    agent = Agent(
        tenant_id=tenant_id,
        name=name,
        description=description,
        source_id=source_id,
        status="pending",
        enrollment_token_hash=_hash(token),
        enrollment_expires_at=expires_at,
        enrolled_by_user_id=enrolled_by_user_id,
    )
    db.add(agent)
    await db.flush()

    await audit_service.emit(
        db,
        tenant_id=tenant_id,
        event_type="agent.enrolled",
        actor_user_id=enrolled_by_user_id,
        subject_type="agent",
        subject_id=agent.id,
        metadata={
            "agent_name": name,
            "source_id": str(source_id),
        },
        message=f"agent {name!r} enrolled",
    )

    return agent, token


async def register_agent(
    db: AsyncSession,
    *,
    enrollment_token: str,
    agent_metadata: dict[str, Any] | None,
) -> tuple[Agent, str]:
    """
    Agent action. Exchanges a one-time enrollment token for a long-lived
    bearer credential. The enrollment token is cleared after use.
    """
    token_hash = _hash(enrollment_token)
    agent = (
        await db.execute(
            select(Agent).where(Agent.enrollment_token_hash == token_hash)
        )
    ).scalar_one_or_none()
    if agent is None:
        raise AgentError("invalid enrollment token")
    if agent.status != "pending":
        raise AgentError(
            f"agent is in status {agent.status!r}, expected 'pending'"
        )
    if (
        agent.enrollment_expires_at is None
        or agent.enrollment_expires_at <= datetime.now(UTC)
    ):
        raise AgentError("enrollment token expired")

    credential = secrets.token_urlsafe(CREDENTIAL_BYTES)
    agent.credential_hash = _hash(credential)
    agent.enrollment_token_hash = None
    agent.enrollment_expires_at = None
    agent.status = "active"
    agent.registered_at = datetime.now(UTC)
    agent.last_seen_at = datetime.now(UTC)
    if agent_metadata:
        agent.agent_metadata = dict(agent_metadata)
    await db.flush()

    await audit_service.emit(
        db,
        tenant_id=agent.tenant_id,
        event_type="agent.registered",
        subject_type="agent",
        subject_id=agent.id,
        metadata={"agent_name": agent.name},
        message=f"agent {agent.name!r} registered",
    )

    return agent, credential


async def authenticate_agent(
    db: AsyncSession,
    *,
    credential: str,
) -> Agent:
    """
    Look up an agent by bearer credential. Returns the Agent row.
    Raises AgentError if invalid or inactive.
    """
    credential_hash = _hash(credential)
    agent = (
        await db.execute(
            select(Agent).where(Agent.credential_hash == credential_hash)
        )
    ).scalar_one_or_none()
    if agent is None:
        raise AgentError("invalid credential")
    if agent.status != "active":
        raise AgentError(f"agent is {agent.status!r}")
    return agent


async def heartbeat(
    db: AsyncSession,
    *,
    agent: Agent,
    agent_metadata: dict[str, Any] | None = None,
) -> Agent:
    """
    Update last_seen_at. Optionally merge fresh metadata (e.g. agent
    version upgraded, hostname changed).
    """
    agent.last_seen_at = datetime.now(UTC)
    if agent_metadata:
        merged = dict(agent.agent_metadata or {})
        merged.update(agent_metadata)
        agent.agent_metadata = merged
    await db.flush()
    return agent


async def process_sync_batch(
    db: AsyncSession,
    *,
    agent: Agent,
    files: list[dict[str, Any]],
) -> dict[str, int]:
    """
    Process a batch of file observations from the agent.

    Each file dict: {path, content_hash, byte_size, mtime, ctime, status}
      status: "new" | "changed" | "unchanged" | "deleted"

    Sansa calls observe_file() for each, which handles idempotency via the
    unique constraint on (tenant_id, source_id, path, content_hash).

    Returns counts.
    """
    counts = {"new": 0, "changed": 0, "unchanged": 0, "deleted": 0, "skipped": 0}

    for spec in files:
        path = spec.get("path")
        content_hash = spec.get("content_hash")
        if not path or not content_hash:
            counts["skipped"] += 1
            continue

        status = spec.get("status", "new")
        if status == "deleted":
            # Deletion handling is deferred. Record nothing for now; a
            # future revision can mark the latest observation as deleted.
            counts["deleted"] += 1
            continue

        file_mtime = _parse_dt(spec.get("mtime"))
        file_ctime = _parse_dt(spec.get("ctime"))

        record = await observe_file(
            db,
            tenant_id=agent.tenant_id,
            source_id=agent.source_id,
            path=path,
            content_hash=content_hash,
            byte_size=spec.get("byte_size"),
            file_mtime=file_mtime,
            file_ctime=file_ctime,
            storage_key=None,   # metadata-only; content moved on ingest
        )
        counts[record.status] = counts.get(record.status, 0) + 1

    agent.last_seen_at = datetime.now(UTC)
    await db.flush()
    return counts


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


async def get_agent(
    db: AsyncSession, *, agent_id: uuid.UUID, tenant_id: uuid.UUID
) -> Agent | None:
    return (
        await db.execute(
            select(Agent).where(
                Agent.id == agent_id,
                Agent.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()


async def list_agents(
    db: AsyncSession, *, tenant_id: uuid.UUID
) -> list[Agent]:
    stmt = (
        select(Agent)
        .where(Agent.tenant_id == tenant_id)
        .order_by(Agent.created_at.desc())
    )
    return list((await db.execute(stmt)).scalars().all())