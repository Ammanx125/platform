"""add browser extraction models

Revision ID: b6c9d3e4f127
Revises: f4f0ba164e82
Create Date: 2026-10-14

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "b6c9d3e4f127"
down_revision: str | None = "f4f0ba164e82"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "browser_authorizations",
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("authorized_by_user_id", sa.UUID(), nullable=True),
        sa.Column("approved_purpose", sa.Text(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("scope", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence_ref", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by_user_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
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
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["data_sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["authorized_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_browser_authorizations_active_origin",
        "browser_authorizations",
        ["tenant_id", "source_id", "origin"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        op.f("ix_browser_authorizations_source_id"),
        "browser_authorizations",
        ["source_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_browser_authorizations_authorized_by_user_id"),
        "browser_authorizations",
        ["authorized_by_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_browser_authorizations_revoked_by_user_id"),
        "browser_authorizations",
        ["revoked_by_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_browser_authorizations_status"),
        "browser_authorizations",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_browser_authorizations_tenant_id"),
        "browser_authorizations",
        ["tenant_id"],
        unique=False,
    )

    op.create_table(
        "browser_extraction_profiles",
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("authorization_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("report_identifier", sa.String(length=500), nullable=False),
        sa.Column(
            "extraction_instructions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "expected_columns",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("pagination", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("limits", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("snapshot_mode", sa.String(length=20), nullable=False),
        sa.Column("active_ingestion_job_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
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
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["data_sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["authorization_id"],
            ["browser_authorizations.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["active_ingestion_job_id"], ["ingestion_jobs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_browser_profiles_tenant_name"),
        sa.UniqueConstraint(
            "tenant_id", "source_id", name="uq_browser_profiles_tenant_source"
        ),
    )
    op.create_index(
        op.f("ix_browser_extraction_profiles_source_id"),
        "browser_extraction_profiles",
        ["source_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_browser_extraction_profiles_authorization_id"),
        "browser_extraction_profiles",
        ["authorization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_browser_extraction_profiles_active_ingestion_job_id"),
        "browser_extraction_profiles",
        ["active_ingestion_job_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_browser_extraction_profiles_status"),
        "browser_extraction_profiles",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_browser_extraction_profiles_tenant_id"),
        "browser_extraction_profiles",
        ["tenant_id"],
        unique=False,
    )

    op.create_table(
        "browser_extraction_runs",
        sa.Column("profile_id", sa.UUID(), nullable=False),
        sa.Column("agent_job_id", sa.UUID(), nullable=True),
        sa.Column("ingestion_job_id", sa.UUID(), nullable=True),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("expected_sha256", sa.String(length=64), nullable=True),
        sa.Column("actual_sha256", sa.String(length=64), nullable=True),
        sa.Column("expected_row_count", sa.Integer(), nullable=True),
        sa.Column("actual_row_count", sa.Integer(), nullable=True),
        sa.Column(
            "completeness_report",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
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
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["profile_id"], ["browser_extraction_profiles.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["agent_job_id"], ["agent_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["ingestion_job_id"], ["ingestion_jobs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "profile_id",
            "run_id",
            name="uq_browser_runs_tenant_profile_run",
        ),
        sa.UniqueConstraint("agent_job_id", name="uq_browser_extraction_runs_agent_job_id"),
    )
    op.create_index(
        op.f("ix_browser_extraction_runs_profile_id"),
        "browser_extraction_runs",
        ["profile_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_browser_extraction_runs_ingestion_job_id"),
        "browser_extraction_runs",
        ["ingestion_job_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_browser_extraction_runs_status"),
        "browser_extraction_runs",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_browser_extraction_runs_tenant_id"),
        "browser_extraction_runs",
        ["tenant_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_browser_extraction_runs_tenant_id"),
        table_name="browser_extraction_runs",
    )
    op.drop_index(
        op.f("ix_browser_extraction_runs_status"),
        table_name="browser_extraction_runs",
    )
    op.drop_index(
        op.f("ix_browser_extraction_runs_ingestion_job_id"),
        table_name="browser_extraction_runs",
    )
    op.drop_index(
        op.f("ix_browser_extraction_runs_profile_id"),
        table_name="browser_extraction_runs",
    )
    op.drop_table("browser_extraction_runs")

    op.drop_index(
        op.f("ix_browser_extraction_profiles_tenant_id"),
        table_name="browser_extraction_profiles",
    )
    op.drop_index(
        op.f("ix_browser_extraction_profiles_status"),
        table_name="browser_extraction_profiles",
    )
    op.drop_index(
        op.f("ix_browser_extraction_profiles_active_ingestion_job_id"),
        table_name="browser_extraction_profiles",
    )
    op.drop_index(
        op.f("ix_browser_extraction_profiles_authorization_id"),
        table_name="browser_extraction_profiles",
    )
    op.drop_index(
        op.f("ix_browser_extraction_profiles_source_id"),
        table_name="browser_extraction_profiles",
    )
    op.drop_table("browser_extraction_profiles")

    op.drop_index(
        op.f("ix_browser_authorizations_tenant_id"),
        table_name="browser_authorizations",
    )
    op.drop_index(
        op.f("ix_browser_authorizations_status"),
        table_name="browser_authorizations",
    )
    op.drop_index(
        op.f("ix_browser_authorizations_revoked_by_user_id"),
        table_name="browser_authorizations",
    )
    op.drop_index(
        op.f("ix_browser_authorizations_authorized_by_user_id"),
        table_name="browser_authorizations",
    )
    op.drop_index(
        op.f("ix_browser_authorizations_source_id"),
        table_name="browser_authorizations",
    )
    op.drop_index(
        "uq_browser_authorizations_active_origin",
        table_name="browser_authorizations",
    )
    op.drop_table("browser_authorizations")
