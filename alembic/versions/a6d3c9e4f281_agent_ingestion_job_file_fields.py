"""add per-file fields to agent ingestion jobs

Revision ID: a6d3c9e4f281
Revises: 66ec3e4a1e3f
Create Date: 2026-10-03 08:21:00

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "a6d3c9e4f281"
down_revision: str | None = "66ec3e4a1e3f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ingestion_jobs",
        sa.Column("storage_key", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "ingestion_jobs",
        sa.Column("pending_path", sa.String(length=1000), nullable=True),
    )
    op.add_column(
        "ingestion_jobs",
        sa.Column("pending_hash", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ingestion_jobs", "pending_hash")
    op.drop_column("ingestion_jobs", "pending_path")
    op.drop_column("ingestion_jobs", "storage_key")
