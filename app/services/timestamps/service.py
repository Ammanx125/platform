# app/services/timestamps/service.py
"""
Timestamps service.

Two responsibilities:
  1. Record ingestion timestamps for every StagedRow on job completion.
  2. Provide helpers for connectors to record content/stream/file timestamps.

Both are idempotent. Re-running over the same rows is a no-op (the
UniqueConstraint on (staged_row_id, kind) makes the conflict explicit).
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.dataset import StagedRow
from app.db.models.timestamps import RowTimestamp
from app.services.timestamps.constants import ALL_BASES, TimeBasis


def _validate_kind(kind: str) -> None:
    if kind not in ALL_BASES:
        raise ValueError(
            f"unknown timestamp kind {kind!r}; allowed: {sorted(ALL_BASES)}"
        )


def _parse_content_timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("date value is empty")
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            parsed = datetime.combine(date.fromisoformat(text), datetime.min.time())
    else:
        raise ValueError(f"unsupported date value {value!r}")
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


async def record_ingestion_timestamps_for_job(
    db: AsyncSession, *, job_id: uuid.UUID
) -> int:
    """
    Insert one 'ingestion' RowTimestamp per staged row of the given job,
    using the row's created_at (which the StagedRow already has).

    Idempotent — uses ON CONFLICT DO NOTHING so re-running is safe.

    Returns the number of rows inserted (excludes conflicts).
    """
    rows = (
        await db.execute(
            select(StagedRow.id, StagedRow.tenant_id, StagedRow.created_at)
            .where(StagedRow.job_id == job_id)
        )
    ).all()
    if not rows:
        return 0

    values = [
        {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "staged_row_id": row_id,
            "kind": TimeBasis.INGESTION.value,
            "timestamp": created_at,
            "context": None,
        }
        for row_id, tenant_id, created_at in rows
    ]

    stmt = pg_insert(RowTimestamp).values(values).on_conflict_do_nothing(
        index_elements=["staged_row_id", "kind"],
    ).returning(RowTimestamp.id)
    result = await db.execute(stmt)
    await db.flush()
    return len(result.scalars().all())


async def record_content_timestamps_for_job(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    date_column: str,
) -> int:
    """Record a configured source date column as each row's content time."""
    rows = (
        await db.execute(
            select(
                StagedRow.id,
                StagedRow.tenant_id,
                StagedRow.row_number,
                StagedRow.raw_data,
            ).where(StagedRow.job_id == job_id)
        )
    ).all()
    values: list[dict] = []
    for row_id, tenant_id, row_number, raw_data in rows:
        raw_value = raw_data.get(date_column)
        if raw_value is None or (isinstance(raw_value, str) and not raw_value.strip()):
            continue
        try:
            timestamp = _parse_content_timestamp(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"invalid content date in row {row_number}, column "
                f"{date_column!r}: {exc}"
            ) from exc
        values.append({
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "staged_row_id": row_id,
            "kind": TimeBasis.CONTENT.value,
            "timestamp": timestamp,
            "context": date_column,
        })

    if not values:
        raise ValueError(
            f"no content timestamps found for configured date column "
            f"{date_column!r}"
        )
    stmt = pg_insert(RowTimestamp).values(values).on_conflict_do_nothing(
        index_elements=["staged_row_id", "kind"],
    ).returning(RowTimestamp.id)
    result = await db.execute(stmt)
    await db.flush()
    return len(result.scalars().all())


async def record_row_timestamp(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    staged_row_id: uuid.UUID,
    kind: str,
    timestamp: datetime,
    context: str | None = None,
) -> None:
    """
    Record one timestamp for one row. Idempotent — same (row, kind) is a
    no-op. Connectors call this for content/stream/file timestamps they
    can derive.
    """
    _validate_kind(kind)
    stmt = (
        pg_insert(RowTimestamp)
        .values({
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "staged_row_id": staged_row_id,
            "kind": kind,
            "timestamp": timestamp,
            "context": context,
        })
        .on_conflict_do_nothing(index_elements=["staged_row_id", "kind"])
    )
    await db.execute(stmt)


async def get_timestamps_for_row(
    db: AsyncSession, *, staged_row_id: uuid.UUID
) -> dict[str, datetime]:
    """Return {kind: timestamp} for one row."""
    rows = (
        await db.execute(
            select(RowTimestamp.kind, RowTimestamp.timestamp).where(
                RowTimestamp.staged_row_id == staged_row_id
            )
        )
    ).all()
    return {kind: ts for kind, ts in rows}