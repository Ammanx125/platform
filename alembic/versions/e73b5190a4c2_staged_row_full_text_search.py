"""add indexed search text to staged rows

Revision ID: e73b5190a4c2
Revises: a6d3c9e4f281
Create Date: 2026-10-04

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "e73b5190a4c2"
down_revision: str | None = "a6d3c9e4f281"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "staged_rows",
        sa.Column("search_text", sa.Text(), nullable=True),
    )
    op.execute(
        "UPDATE staged_rows SET search_text = raw_data::text "
        "WHERE search_text IS NULL"
    )
    op.alter_column("staged_rows", "search_text", nullable=False)
    op.create_index(
        "ix_staged_rows_search_text_gin",
        "staged_rows",
        [sa.text("to_tsvector('simple', search_text)")],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_staged_rows_search_text_gin",
        table_name="staged_rows",
    )
    op.drop_column("staged_rows", "search_text")
