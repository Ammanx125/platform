# app/services/datasets/scope.py
"""
Query scoping for staged rows.

Sansa has two ways to read a source's staged rows:

  'append'          - all rows from every ingestion job. This is the
                      default and matches the historical behavior of
                      every existing source type.

  'replace_active'  - only rows from the profile's currently active
                      ingestion job. Used for browser extraction
                      snapshots, where each snapshot is a complete
                      restatement of the dataset and summing historical
                      copies would double-count.

Every analytics, forecast, anomaly, and structured-retrieval query that
reads StagedRow must go through this helper rather than filtering on
source_id alone. There is exactly one place where the rule lives; each
call site adds one line.

A single query may not mix modes. Attempting to read append and
replace_active sources in the same query raises SnapshotScopeError. This
is a business-level constraint: mixing a live feed with a snapshot in one
aggregate would silently mix two different semantics for "the data." If
this restriction ever becomes a real limitation, it can be lifted by
splitting the query per mode and unioning the results, but the current
product does not need that.
"""
from __future__ import annotations

import uuid

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from app.db.models.browser import BrowserExtractionProfile
from app.db.models.dataset import DataSource, IngestionJob, StagedRow
from app.db.models.timestamps import FileObservation


async def deleted_snapshot_filter(
    db: AsyncSession, *, tenant_id: uuid.UUID
) -> list[ColumnElement[bool]]:
    """
    Exclude rows whose source file has been tombstoned as deleted.

    A deleted file must stop powering analytics. The customer may have
    removed it precisely because it was wrong; continuing to derive KPIs
    from it makes the platform assert numbers nobody can see or correct.

    Only observations that record an ingestion job are considered: a
    metadata-only observation never produced rows, and treating it as
    deleted would hide nothing.
    """
    deleted_job_ids = (
        await db.execute(
            select(FileObservation.ingestion_job_id)
            .where(
                FileObservation.tenant_id == tenant_id,
                FileObservation.status == "deleted",
                FileObservation.ingestion_job_id.is_not(None),
            )
        )
    ).scalars().all()

    if not deleted_job_ids:
        return []
    return [~StagedRow.job_id.in_(list(deleted_job_ids))]


class SnapshotScopeError(Exception):
    """Raised when a query mixes snapshot modes or references an
    inconsistent source configuration."""


async def active_snapshot_filter(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_ids: list[uuid.UUID],
) -> list[ColumnElement[bool]]:
    """
    Return SQLAlchemy WHERE clauses that constrain a StagedRow query to
    the correct rows for the given sources.

    - Empty source_ids: returns [] — caller is responsible for the empty
      case, but a query with no sources should already have short-circuited.
    - All 'append': returns [] — every staged row for these sources is
      eligible, matching existing behavior.
    - All 'replace_active': returns [StagedRow.job_id.in_(active_job_ids)].
      Sources with no active snapshot yet contribute no job id; the final
      clause matches nothing for them, which is correct: a source that
      has never produced a snapshot has no active rows to read.
    - All 'upsert': keeps the newest row per business key, independently
      for each source and its configured key column.
    - Mixed: raises SnapshotScopeError.

    The helper is async because resolving the active pointer requires a
    DB read. The returned clauses are synchronous and can be added to any
    StagedRow query via `stmt = stmt.where(*clauses)`.

    The caller must already have validated that all source_ids belong to
    tenant_id; this function does not re-check ownership. That validation
    is done once, at the boundary, by EvaluationContext.load and
    build_series (via their source-resolution SELECTs).
    """
    if not source_ids:
        return []

    # Load mode for each requested source, plus the active pointer for
    # replace_active sources.
    rows = (
        await db.execute(
            select(
                DataSource.id,
                DataSource.snapshot_mode,
                DataSource.upsert_key_column,
                BrowserExtractionProfile.active_ingestion_job_id,
            )
            .select_from(DataSource)
            .outerjoin(
                BrowserExtractionProfile,
                BrowserExtractionProfile.source_id == DataSource.id,
            )
            .where(
                DataSource.tenant_id == tenant_id,
                DataSource.id.in_(source_ids),
            )
        )
    ).all()

    found_source_ids = {r.id for r in rows}
    missing = set(source_ids) - found_source_ids
    if missing:
        raise SnapshotScopeError(
            f"source(s) not found for tenant: {sorted(str(s) for s in missing)}"
        )

    modes = {r.snapshot_mode for r in rows}
    if len(modes) > 1:
        raise SnapshotScopeError(
            "cannot mix append and replace_active sources in one query; "
            f"modes present: {sorted(modes)}"
        )
    mode = modes.pop()

    if mode == "append":
        return []

    if mode == "upsert":
        source_clauses = [
            and_(
                StagedRow.source_id == r.id,
                *snapshot_key_filter(key_column=r.upsert_key_column),
            )
            for r in rows
        ]
        return [or_(*source_clauses)]

    # mode == 'replace_active'
    active_job_ids: list[uuid.UUID] = []
    for r in rows:
        if r.active_ingestion_job_id is None:
            raise SnapshotScopeError(
                f"source {r.id} is replace_active but has no active "
                "snapshot pointer; the profile's active_ingestion_job_id "
                "must be set before analytics may read this source"
            )
        active_job_ids.append(r.active_ingestion_job_id)

    return [StagedRow.job_id.in_(active_job_ids)]


def snapshot_key_filter(
    *, key_column: str | None
) -> list[ColumnElement[bool]]:
    """
    Rows for an upsert source, reduced to the newest row per business key.

    Why this exists: a weekly full export in append mode double-counts.
    Uploading sales totals 100k, 120k, 130k appends to 350k when the true
    figure is 130k. 'replace_active' fixes it for whole-snapshot sources,
    but an ERP sync sends changes, not snapshots. Upsert keeps the newest
    row per key across every job, so an updated record replaces the old one
    without discarding records the export did not mention.

    `key_column` names the raw_data field that identifies a business
    record (an order id, an invoice number). Without it an upsert source
    cannot be summarised honestly, so the call raises rather than
    returning every row and double-counting silently.

    The filter is a correlated NOT EXISTS: keep a row only if no other row
    for the same source and key was ingested later or, on a tie, has a
    higher row_number. It reads every row's job timestamp, which is why
    upsert sources are expected to be moderate in size.
    """
    if not key_column:
        raise SnapshotScopeError(
            "an 'upsert' source must declare a key column; otherwise the "
            "newest record per business key cannot be determined"
        )

    newer = aliased(StagedRow)
    newer_job = aliased(IngestionJob)
    current_job = aliased(IngestionJob)
    newer_is_later = or_(
        newer_job.created_at > current_job.created_at,
        and_(
            newer_job.created_at == current_job.created_at,
            or_(
                newer.job_id > StagedRow.job_id,
                and_(
                    newer.job_id == StagedRow.job_id,
                    newer.row_number > StagedRow.row_number,
                ),
            ),
        ),
    )
    return [
        ~(
            select(newer.id)
            .join(newer_job, newer_job.id == newer.job_id)
            .join(current_job, current_job.id == StagedRow.job_id)
            .where(
                newer.source_id == StagedRow.source_id,
                newer.raw_data[key_column].astext
                == StagedRow.raw_data[key_column].astext,
                newer_is_later,
            )
            .exists()
        )
    ]


async def active_snapshot_filter_for_documents(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_ids: list[uuid.UUID],
) -> list[ColumnElement[bool]]:
    """
    Same rule, but for Document queries.

    Documents don't carry a job_id column the way StagedRows do — the
    "which ingestion job produced this document" link for a browser
    snapshot is not currently stored on Document. For v1 we scope
    Document queries the same way as StagedRows when the source is
    replace_active, but this will require a follow-up: either a
    source_id + created_at window, or a new Document.ingestion_job_id
    column. Today, browser snapshots produce StagedRows (CSV), not
    Documents, so this function is present for symmetry and future use.

    If a caller currently needs Document scoping for replace_active
    sources, they should filter by source_id and by the active snapshot's
    created_at lower bound. That refinement is not required by any
    existing caller.
    """
    if not source_ids:
        return []
    # Delegates to the same mode check so we never silently diverge.
    return await active_snapshot_filter(
        db, tenant_id=tenant_id, source_ids=source_ids
    )