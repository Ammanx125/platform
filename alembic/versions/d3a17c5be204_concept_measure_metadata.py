"""concept measure metadata

Revision ID: d3a17c5be204
Revises: c4f7a2b91d63
Create Date: 2026-10-30 10:00:00.000000

Adds unit and currency to canonical_concepts so a concept's values can be
labelled ("Revenue (ETB)") and explained without consulting the KPI that
consumed them.

display_name, description, and synonyms already existed and are unchanged.
No backfill is possible here: unit/currency are catalog semantics, not
derivable from existing rows. Re-running the concept seed populates them
for the built-in catalog and installed industry packs.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d3a17c5be204"
down_revision: str | None = "c4f7a2b91d63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "canonical_concepts",
        sa.Column("unit", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "canonical_concepts",
        sa.Column("currency", sa.String(length=10), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("canonical_concepts", "currency")
    op.drop_column("canonical_concepts", "unit")
