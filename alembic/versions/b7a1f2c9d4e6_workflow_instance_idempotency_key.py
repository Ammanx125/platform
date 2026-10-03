"""add workflow instance idempotency key

Revision ID: b7a1f2c9d4e6
Revises: a9c26dfd3f18
Create Date: 2026-10-01

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "b7a1f2c9d4e6"
down_revision: str | None = "a9c26dfd3f18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflow_instances",
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
    )
    op.create_index(
        op.f("ix_workflow_instances_idempotency_key"),
        "workflow_instances",
        ["idempotency_key"],
        unique=False,
    )
    op.create_index(
        "ix_workflow_instances_idempotency",
        "workflow_instances",
        ["tenant_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workflow_instances_idempotency",
        table_name="workflow_instances",
    )
    op.drop_index(
        op.f("ix_workflow_instances_idempotency_key"),
        table_name="workflow_instances",
    )
    op.drop_column("workflow_instances", "idempotency_key")