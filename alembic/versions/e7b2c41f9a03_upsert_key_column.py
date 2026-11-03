"""data sources: upsert key column

Revision ID: e7b2c41f9a03
Revises: d3a17c5be204
Create Date: 2026-11-03 10:00:00.000000

Adds the business-key column needed by the 'upsert' snapshot mode.

With only 'append' and 'replace_active', a tenant re-uploading a complete
sales export every week either double-counts (append: 100k + 120k + 130k)
or loses whatever the newest export omitted. Upsert keeps the newest row
per business key, so it needs to know which column identifies a record.

Existing sources keep snapshot_mode='append' and a NULL key, so this
migration changes no behaviour on its own.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7b2c41f9a03"
down_revision: str | None = "d3a17c5be204"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "data_sources",
        sa.Column("upsert_key_column", sa.String(length=200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("data_sources", "upsert_key_column")
