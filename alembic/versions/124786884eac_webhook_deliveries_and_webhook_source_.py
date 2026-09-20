"""webhook deliveries and webhook source fields

Revision ID: 124786884eac
Revises: 76222380cd3d
Create Date: 2026-09-20 21:01:11.133323

"""
from __future__ import annotations

from collections.abc import Sequence



revision: str = '124786884eac'
down_revision: str | None = '76222380cd3d'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The parent revision already creates these webhook objects.
    pass


def downgrade() -> None:
    pass