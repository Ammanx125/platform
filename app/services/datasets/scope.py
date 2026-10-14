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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.db.models.browser import BrowserExtractionProfile
from app.db.models.dataset import DataSource, StagedRow


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