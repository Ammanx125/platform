"""action records: idempotency key and attempt count

Revision ID: b8d34ae0c517
Revises: a5e1c73bd902
Create Date: 2026-11-03 13:00:00.000000

External side effects are not transactional with the database. Gmail can
deliver an email and the transaction that would have recorded it can fail;
a retry then sends a second copy. These columns make an attempt visible
before the external call and give the provider a stable key to deduplicate
on.

Backfill: every existing action gets a key derived from its id, which is
unique by construction and cannot change. Attempts stay at 0, meaning
"no execution was ever recorded", which matches the current rows.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8d34ae0c517"
down_revision: str | None = "a5e1c73bd902"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "action_records",
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "action_records",
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_unique_constraint(
        "uq_action_records_idempotency_key",
        "action_records",
        ["idempotency_key"],
    )
    op.create_index(
        "ix_action_records_idempotency_key",
        "action_records",
        ["idempotency_key"],
    )

    op.execute(
        """
        UPDATE action_records
        SET idempotency_key = 'action:' || id::text
        WHERE idempotency_key IS NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_action_records_idempotency_key", "action_records")
    op.drop_constraint(
        "uq_action_records_idempotency_key", "action_records", type_="unique"
    )
    op.drop_column("action_records", "attempt_count")
    op.drop_column("action_records", "idempotency_key")
