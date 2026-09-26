# app/db/models/workflow.py
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class WorkflowInstance(Base, UUIDPrimaryKeyMixin, TimestampMixin, TenantMixin):
    """
    One execution of a workflow.

    A workflow is a stateful, domain-oriented process. The definition is
    code (registered like actions); the instance is data (this row).

    status lifecycle:
      pending                 — created, not yet started
      running                 — a step is currently executing
      waiting_workflow_approval — paused at an await_approval step
      waiting_action_approval — paused at an await_action step
      waiting_input           — paused, awaiting arbitrary input (deferred)
      completed               — all steps succeeded
      rejected                — a workflow-level approval was rejected
      failed                  — a step failed and the workflow didn't recover
      cancelled               — cancelled by a user

    step_states is an ordered JSONB list:
      [
        {"name": "analyze", "status": "completed", "output": {...},
         "started_at": "...", "finished_at": "...", "error": null},
        {"name": "await_approval", "status": "waiting",
         "started_at": "...", "finished_at": null, "error": null},
        ...
      ]

    workflow_context is a JSONB bag carried between steps. Steps read from
    it and write to it; it's how a later step sees an earlier step's output
    without a schema per workflow.

    An instance is uniquely identified by (tenant_id, workflow_key,
    trigger_metadata->>'request_id') at creation time, so a retried request
    doesn't produce duplicates. This is enforced in the service, not the DB,
    because the identity is composite with JSONB.
    """
    __tablename__ = "workflow_instances"

    workflow_key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)

    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="pending", index=True
    )
    current_step: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # Ordered list of step states (see docstring).
    step_states: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # Bag passed between steps.
    workflow_context: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Who or what triggered this instance.
    triggered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    trigger_type: Mapped[str] = mapped_column(
        String(40), nullable=False, default="manual"
    )  # manual | decision | schedule | event
    trigger_metadata: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    decision_run_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("decision_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # For waiting_workflow_approval: which step is paused.
    pending_approval_step: Mapped[str | None] = mapped_column(
        String(120), nullable=True
    )
    # For waiting_action_approval: which action we're waiting on.
    pending_action_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("action_records.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index("ix_workflow_instances_tenant_status", "tenant_id", "status"),
    )

class WorkflowTrigger(Base, UUIDPrimaryKeyMixin, TimestampMixin, TenantMixin):
    """
    A per-tenant binding: "when an event of type X occurs, start workflow W."

    event_type_filter: exact match against Event.event_type, or "*" to match all.
    source_id_filter: if set, only events from this source trigger the workflow.
    enabled: toggle without deleting.
    cooldown_seconds: don't retrigger this workflow for the same tenant
        within this window. Used to avoid a burst of events (e.g. 100
        stock-drop anomalies in one second) spawning 100 workflow runs.
    trigger_params: passed to start_workflow as trigger_metadata, plus
        optional source_ids/workflow_params overrides.
    """
    __tablename__ = "workflow_triggers"

    workflow_key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    event_type_filter: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    source_id_filter: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("data_sources.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    cooldown_seconds: Mapped[int] = mapped_column(nullable=False, default=60)
    trigger_params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    last_fired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "workflow_key", "event_type_filter", "source_id_filter",
            name="uq_workflow_triggers_binding",
        ),
    )