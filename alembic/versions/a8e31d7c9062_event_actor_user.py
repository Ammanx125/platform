"""add optional actor identity to events

Revision ID: a8e31d7c9062
Revises: 2d89d2ef0a8b
Create Date: 2026-09-26

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "a8e31d7c9062"
down_revision: str | None = "2d89d2ef0a8b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_events_user_id_users",
        "events",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_events_user_id", "events", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_events_user_id", table_name="events")
    op.drop_constraint("fk_events_user_id_users", "events", type_="foreignkey")
    op.drop_column("events", "user_id")