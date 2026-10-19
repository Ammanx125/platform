# app/db/models/browser.py
"""
Browser extraction models.

Three tables, each with a distinct responsibility:

  BrowserAuthorization      - who approved what, for which origin, with what
                              evidence. Decides whether the platform may
                              issue an extraction job at all.

  BrowserExtractionProfile  - what to extract, from where, with what limits,
                              for which source. Defines the exact report.

  BrowserExtractionRun      - one attempt. Carries the state machine, the
                              manifest verification results, and the link
                              to the IngestionJob that was created once the
                              snapshot arrived and validated.

These models deliberately do NOT duplicate the ingestion path. The CSV the
browser agent produces flows through the existing agent upload endpoint,
FileObservation, and IngestionJob. The run row is provenance; it is not an
alternative ingestion record.

The DataSource that a profile targets has snapshot_mode='replace_active'.
That is what tells the analytics and retrieval scope helper to consider
only rows from the profile's active_ingestion_job_id.
"""
from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin

# Run states that count as "in flight" for the one-run-per-profile rule.
# Defined here once so the model, the service, and any future migration
# share the same set. Do not redeclare this list anywhere else.
IN_FLIGHT_RUN_STATES: frozenset[str] = frozenset({
    "scheduled",
    "extracting",
    "manifest_received",
    "awaiting_upload",
    "validating",
    "activating",
})

# Terminal states. A run in one of these does not block scheduling.
TERMINAL_RUN_STATES: frozenset[str] = frozenset({
    "succeeded",
    "active",
    "superseded",
    "failed",
})

# The full valid state set. Used by tests and by the state-transition
# helpers. The state machine is:
#
#   scheduled -> extracting -> manifest_received -> awaiting_upload
#             -> validating -> activating -> succeeded | active
#   any in-flight state -> failed (via reaper, error, or explicit fail)
#   succeeded -> active -> superseded
#
# 'succeeded' means the run finished and its snapshot was ingested
# successfully. 'active' means the profile's active_ingestion_job_id
# currently points at this run's ingestion job. In v1 these two states
# coincide for the same run because activation happens immediately after
# ingestion succeeds; they are kept separate so a future case (validation
# succeeded but activation was deferred) can be represented honestly.
ALL_RUN_STATES: frozenset[str] = IN_FLIGHT_RUN_STATES | TERMINAL_RUN_STATES

# ---------- BrowserAuthorization ----------

class BrowserAuthorization(Base, UUIDPrimaryKeyMixin, TimestampMixin, TenantMixin):
    """
    A record that the tenant authorized browser-based extraction for a
    specific source and origin.

    Authorization records are auditable. They are revoked, not deleted:
    status='revoked' + revoked_at + revoked_by_user_id. Historical runs
    still reference the authorization they were issued under.

    Only ONE active authorization may exist per (tenant, source, origin).
    The partial unique index enforces this; revoked rows are excluded.

    scope is JSONB and structured. Example:
        {
            "report_patterns": ["Sales > Daily Report", "Inventory > Stock"],
            "notes": "Approved by IT on 2026-09-01"
        }
    Do NOT store credentials, tokens, or session data here.
    """
    __tablename__ = "browser_authorizations"

    source_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("data_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    authorized_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    revoked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    origin: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    approved_purpose: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    evidence_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", index=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    source = relationship("DataSource", lazy="selectin")

    __table_args__ = (
        Index(
            "uq_browser_authorizations_active_origin",
            "tenant_id",
            "source_id",
            "origin",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )


# ---------- BrowserExtractionProfile ----------

class BrowserExtractionProfile(Base, UUIDPrimaryKeyMixin, TimestampMixin, TenantMixin):
    """
    A specific extraction: which report, which steps, what to expect.

    v1 constraint: one profile per source. Enforced by
    UNIQUE(tenant_id, source_id). Generalizing to multiple profiles per
    source is a future change that would relax this constraint; doing it
    now would risk mixing unrelated reports' columns under one source and
    confusing the semantic mappings.

    extraction_instructions is JSONB and describes the steps the agent
    should take. The schema is validated at the API layer against
    BrowserExtractionProfileCreate in app/schemas/browser.py.

    expected_columns is the set of header names the CSV snapshot must
    contain. Validated server-side after upload. Missing or extra columns
    outside tolerance fails validation.

    active_ingestion_job_id is the "active snapshot pointer". Only one
    job's staged rows are read by analytics for this source. The pointer
    is swapped transactionally only after a new snapshot has been
    validated, ingested, and marked ready. On any failure the pointer is
    left alone; the previous snapshot stays active.

    status: 'active' | 'paused' | 'archived'
    """
    __tablename__ = "browser_extraction_profiles"

    source_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("data_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    authorization_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("browser_authorizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    report_identifier: Mapped[str] = mapped_column(String(500), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    extraction_instructions: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    expected_columns: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list
    )
    pagination: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    limits: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    snapshot_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="replace_active"
    )

    active_ingestion_job_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("ingestion_jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", index=True
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "name",
            name="uq_browser_profiles_tenant_name",
        ),
        UniqueConstraint(
            "tenant_id", "source_id",
            name="uq_browser_profiles_tenant_source",
        ),
        Index(
            "ix_browser_profiles_tenant_status",
            "tenant_id", "status",
        ),
    )


# ---------- BrowserExtractionRun ----------

class BrowserExtractionRun(Base, UUIDPrimaryKeyMixin, TimestampMixin, TenantMixin):
    """
    One extraction attempt, from scheduling to activation or failure.

    status lifecycle:

      scheduled          agent job created, agent has not claimed it yet
      extracting         agent claimed the job, is driving the browser
      manifest_received  agent reported success; server validated manifest
      awaiting_upload    server told agent to upload; waiting for bytes
      validating         bytes received; server verifying hash and content
      activating         validated; about to swap active snapshot
      active             snapshot is live; analytics read this run's rows
      superseded         a newer run became active
      failed             extraction or validation failed; previous stays active

    Invariants enforced at the service layer:
      - A run in 'active' has a non-null ingestion_job_id and actual_sha256.
      - At most one run per profile may be in 'active'.
      - Any transition to 'failed' or 'superseded' clears this run from
        the profile's active_ingestion_job_id if it was there.
      - A run never transitions out of 'active' or 'superseded' except
        from 'active' to 'superseded'.

    Uniqueness:
      - UNIQUE(agent_job_id): one job → one run. Hard dedup at the
        platform edge. Even if the agent sends the same manifest twice,
        only one run exists.
      - UNIQUE(tenant_id, profile_id, run_id): lets a logical retry of the
        same extraction (agent kept its local state, wants to reuse the
        same run_id) reuse the existing row, while a genuinely new
        scheduled extraction gets a fresh run_id and a fresh row.

    expected_sha256 / expected_row_count come from the manifest (agent
    claim). actual_sha256 / actual_row_count are computed server-side
    from the uploaded bytes (evidence). They may differ; if they do, the
    run fails validation and completeness_report explains why.

    completeness_report captures what the server actually checked:

        {
          "hash_matches": bool,
          "row_count_matches": bool,
          "columns_present": [str, ...],
          "columns_missing": [str, ...],
          "row_count_within_tolerance": bool,
          "tolerance_pct": float,
          "notes": str | None
        }
    """
    __tablename__ = "browser_extraction_runs"

    profile_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("browser_extraction_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_job_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agent_jobs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    ingestion_job_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("ingestion_jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    run_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="scheduled", index=True
    )

    expected_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actual_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expected_row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    completeness_report: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    profile = relationship("BrowserExtractionProfile", lazy="selectin")

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "profile_id", "run_id",
            name="uq_browser_runs_tenant_profile_run",
        ),
        Index(
            "uq_browser_runs_one_in_flight_per_profile",
            "tenant_id", "profile_id",
            unique=True,
            postgresql_where=sa.text(
                "status IN ('scheduled','extracting','manifest_received',"
                "'awaiting_upload','validating','activating')"
            ),
        ),
        Index(
            "ix_browser_runs_tenant_status",
            "tenant_id", "status",
        ),
        Index(
            "ix_browser_runs_profile_status",
            "profile_id", "status",
        ),
    )