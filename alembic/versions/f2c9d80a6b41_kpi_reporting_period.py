"""kpi definitions: reporting period

Revision ID: f2c9d80a6b41
Revises: e7b2c41f9a03
Create Date: 2026-11-03 11:00:00.000000

Adds the reporting window a KPI covers.

Before this, every KPI summed whatever rows were in scope. An "all-time gross
profit" and a "monthly gross profit" are different numbers that can share a
name, and nothing in the platform could tell them apart. Existing definitions
default to 'all_time', which preserves their current meaning exactly.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2c9d80a6b41"
down_revision: str | None = "e7b2c41f9a03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "kpi_definitions",
        sa.Column(
            "period",
            sa.String(length=20),
            nullable=False,
            server_default="all_time",
        ),
    )
    op.add_column(
        "kpi_definitions",
        sa.Column(
            "period_count",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )


def downgrade() -> None:
    op.drop_column("kpi_definitions", "period_count")
    op.drop_column("kpi_definitions", "period")
