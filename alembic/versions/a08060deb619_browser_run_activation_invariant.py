"""browser run activation invariant

Revision ID: a08060deb619
Revises: ba80a6f679d2
Create Date: 2026-10-27 17:05:32.794419

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = 'a08060deb619'
down_revision: Union[str, None] = 'ba80a6f679d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Alembic autogenerate does not detect CHECK constraints added via
    # CheckConstraint in __table_args__, so this migration is written
    # by hand to match the model declaration:
    #
    #   CheckConstraint(
    #       "(status != 'active') OR "
    #       "(ingestion_job_id IS NOT NULL AND actual_sha256 IS NOT NULL)",
    #       name="ck_browser_runs_active_has_ingestion_and_hash",
    #   )
    #
    # Rationale: a run cannot be the active snapshot without a real
    # ingestion job and a verified hash. Enforced at the DB level so no
    # code path can violate it, even accidentally.
    op.create_check_constraint(
        "ck_browser_runs_active_has_ingestion_and_hash",
        "browser_extraction_runs",
        "(status != 'active') OR "
        "(ingestion_job_id IS NOT NULL AND actual_sha256 IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_browser_runs_active_has_ingestion_and_hash",
        "browser_extraction_runs",
        type_="check",
    )