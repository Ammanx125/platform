"""repair webhook secret column

Revision ID: 2a7c9e4d1f30
Revises: 124786884eac
Create Date: 2026-09-20

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "2a7c9e4d1f30"
down_revision: str | None = "124786884eac"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {
        column["name"]
        for column in sa.inspect(bind).get_columns("data_sources")
    }

    if "webhook_secret_encrypted" in columns:
        return
    if "webhook_secret_hash" in columns:
        op.alter_column(
            "data_sources",
            "webhook_secret_hash",
            new_column_name="webhook_secret_encrypted",
            type_=sa.Text(),
        )
        return

    op.add_column(
        "data_sources",
        sa.Column("webhook_secret_encrypted", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    bind = op.get_bind()
    columns = {
        column["name"]
        for column in sa.inspect(bind).get_columns("data_sources")
    }

    if "webhook_secret_encrypted" in columns:
        op.alter_column(
            "data_sources",
            "webhook_secret_encrypted",
            new_column_name="webhook_secret_hash",
            type_=sa.String(length=64),
        )
