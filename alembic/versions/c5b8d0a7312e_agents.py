"""agent enrollment and credentials

Revision ID: c5b8d0a7312e
Revises: aff65a1049f9
Create Date: 2026-10-02

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "c5b8d0a7312e"
down_revision: str | None = "aff65a1049f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("credential_hash", sa.String(length=64), nullable=True),
        sa.Column("enrollment_token_hash", sa.String(length=64), nullable=True),
        sa.Column("enrollment_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enrolled_by_user_id", sa.UUID(), nullable=True),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "agent_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["enrolled_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["source_id"], ["data_sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agents_credential_hash", "agents", ["credential_hash"], unique=True)
    op.create_index(
        "ix_agents_enrollment_token_hash",
        "agents",
        ["enrollment_token_hash"],
        unique=True,
    )
    op.create_index("ix_agents_last_seen_at", "agents", ["last_seen_at"], unique=False)
    op.create_index("ix_agents_source_id", "agents", ["source_id"], unique=False)
    op.create_index("ix_agents_status", "agents", ["status"], unique=False)
    op.create_index("ix_agents_tenant_id", "agents", ["tenant_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_agents_tenant_id", table_name="agents")
    op.drop_index("ix_agents_status", table_name="agents")
    op.drop_index("ix_agents_source_id", table_name="agents")
    op.drop_index("ix_agents_last_seen_at", table_name="agents")
    op.drop_index("ix_agents_enrollment_token_hash", table_name="agents")
    op.drop_index("ix_agents_credential_hash", table_name="agents")
    op.drop_table("agents")