"""widen chunk embeddings to 1024

Revision ID: cc404502616f
Revises: f8bc0c07e64f
Create Date: 2026-09-21 17:17:58.410223

"""
from __future__ import annotations

from collections.abc import Sequence



revision: str = 'cc404502616f'
down_revision: str | None = 'f8bc0c07e64f'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass