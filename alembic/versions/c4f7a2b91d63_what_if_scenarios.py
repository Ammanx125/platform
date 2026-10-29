"""what-if scenarios

Revision ID: c4f7a2b91d63
Revises: a08060deb619
Create Date: 2026-10-29 10:00:00.000000

Adds the single table behind Scenario Lab. One row per saved what-if
scenario, holding the frozen baseline, the validated assumptions, the
calculated result, and the provenance needed to audit or recompute it.

No existing table is altered: the simulator only reads source data.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c4f7a2b91d63"
down_revision: str | None = "a08060deb619"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "whatif_scenarios",
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("scenario_type", sa.String(length=80), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "assumptions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "baseline_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("stale_reason", sa.String(length=500), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "tenant_id", sa.UUID(), nullable=False
        ),
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
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["data_sources.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_whatif_scenarios_created_by_user_id",
        "whatif_scenarios",
        ["created_by_user_id"],
    )
    op.create_index(
        "ix_whatif_scenarios_scenario_type",
        "whatif_scenarios",
        ["scenario_type"],
    )
    op.create_index("ix_whatif_scenarios_source_id", "whatif_scenarios", ["source_id"])
    op.create_index("ix_whatif_scenarios_status", "whatif_scenarios", ["status"])
    op.create_index("ix_whatif_scenarios_tenant_id", "whatif_scenarios", ["tenant_id"])
    op.create_index(
        "ix_whatif_scenarios_tenant_created",
        "whatif_scenarios",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_whatif_scenarios_tenant_source",
        "whatif_scenarios",
        ["tenant_id", "source_id"],
    )


def downgrade() -> None:
    op.drop_table("whatif_scenarios")
