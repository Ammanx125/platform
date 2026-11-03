"""file observations: link to ingestion job

Revision ID: a5e1c73bd902
Revises: f2c9d80a6b41
Create Date: 2026-11-03 12:00:00.000000

Adds file_observations.ingestion_job_id.

The watcher reports deleted files, and FileObservation already documents a
'deleted' status, but nothing recorded which rows an observation produced.
Without that link a tombstone cannot exclude the rows a deleted spreadsheet
contributed, so a file the customer deleted to remove WRONG data keeps
powering every KPI derived from it.

Backfill: existing observations are matched to the newest succeeded
ingestion job of the same source whose stored file hash equals the
observation's content hash. Unmatched rows stay NULL, which is the safe
default (they are treated as still active).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a5e1c73bd902"
down_revision: str | None = "f2c9d80a6b41"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "file_observations",
        sa.Column(
            "ingestion_job_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_file_observations_ingestion_job_id",
        "file_observations",
        "ingestion_jobs",
        ["ingestion_job_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_file_observations_ingestion_job_id",
        "file_observations",
        ["ingestion_job_id"],
    )

    op.execute(
        """
        UPDATE file_observations AS fo
        SET ingestion_job_id = (
            SELECT ij.id
            FROM ingestion_jobs AS ij
            WHERE ij.source_id = fo.source_id
              AND ij.tenant_id = fo.tenant_id
              AND ij.status = 'succeeded'
              AND ij.pending_hash = fo.content_hash
            ORDER BY ij.finished_at DESC NULLS LAST
            LIMIT 1
        )
        WHERE fo.ingestion_job_id IS NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_file_observations_ingestion_job_id", "file_observations")
    op.drop_constraint(
        "fk_file_observations_ingestion_job_id", "file_observations", type_="foreignkey"
    )
    op.drop_column("file_observations", "ingestion_job_id")
