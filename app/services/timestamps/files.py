# app/services/timestamps/files.py
"""
File observation service.

Called by the upload endpoint (now) and by the on-prem agent (later).
Idempotent per (tenant, source, path, content_hash).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.timestamps import FileObservation


async def observe_file(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    path: str,
    content_hash: str,
    byte_size: int | None = None,
    file_mtime: datetime | None = None,
    file_ctime: datetime | None = None,
    storage_key: str | None = None,
    observed_at: datetime | None = None,
) -> FileObservation:
    """
    Record a file observation.

    If (tenant, source, path, content_hash) already exists, updates
    last_seen_at and returns the existing row with status unchanged.
    Otherwise inserts a new row with status='new' (or 'changed' if a prior
    observation of the same path exists with a different hash).

    Callers that don't know mtime/ctime pass None; those columns stay null.
    """
    now = observed_at or datetime.now(UTC)

    existing = (
        await db.execute(
            select(FileObservation).where(
                FileObservation.tenant_id == tenant_id,
                FileObservation.source_id == source_id,
                FileObservation.path == path,
                FileObservation.content_hash == content_hash,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        existing.last_seen_at = now
        existing.status = "unchanged"
        # If the caller now knows mtime/ctime and we didn't before, fill it in.
        if existing.file_mtime is None and file_mtime is not None:
            existing.file_mtime = file_mtime
        if existing.file_ctime is None and file_ctime is not None:
            existing.file_ctime = file_ctime
        await db.flush()
        return existing

    # Determine status: 'changed' if we've seen this path before with a
    # different hash; 'new' otherwise.
    prior = (
        await db.execute(
            select(FileObservation.id).where(
                FileObservation.tenant_id == tenant_id,
                FileObservation.source_id == source_id,
                FileObservation.path == path,
            ).limit(1)
        )
    ).scalar_one_or_none()
    status = "changed" if prior is not None else "new"

    record = FileObservation(
        tenant_id=tenant_id,
        source_id=source_id,
        path=path,
        content_hash=content_hash,
        byte_size=byte_size,
        file_mtime=file_mtime,
        file_ctime=file_ctime,
        first_seen_at=now,
        last_seen_at=now,
        status=status,
        storage_key=storage_key,
    )
    db.add(record)
    await db.flush()
    return record