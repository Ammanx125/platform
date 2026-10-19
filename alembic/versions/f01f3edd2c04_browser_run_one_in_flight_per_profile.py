"""browser run one in-flight per profile

Revision ID: f01f3edd2c04
Revises: e72ced61d05e
Create Date: 2026-10-14 19:30:31.893459

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'f01f3edd2c04'
down_revision: str | None = 'e72ced61d05e'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_browser_runs_one_in_flight_per_profile",
        "browser_extraction_runs",
        ["tenant_id", "profile_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('scheduled','extracting','manifest_received',"
            "'awaiting_upload','validating','activating')"
        ),
    )

    op.add_column("browser_extraction_profiles", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))

def downgrade() -> None:
    op.drop_column("browser_extraction_profiles", "revision")
    op.drop_index(
        "uq_browser_runs_one_in_flight_per_profile",
        table_name="browser_extraction_runs",
    )