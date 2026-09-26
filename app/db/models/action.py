# app/db/models/action.py
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class ActionRecord(Base, UUIDPrimaryKeyMixin, TimestampMixin, TenantMixin):
    """
    One action proposal, from any source.

    Every LLM-proposed tool call creates an ActionRecord — even if the tool
    is unknown, arguments are invalid, or the user is unauthorized. Rejected
    proposals are evidence in their own right: they show what the model
    wanted to do and why Sansa refused.

    status lifecycle:
            pending_approval → approved → executed | failed | verification_failed
            pending_approval → rejected
            rejected         → terminal
            executed         → terminal (verification passed or was skipped)
            verification_failed → terminal (side effect may have occurred)
            failed           → terminal (execution error before side effect)

    In Step 11a only "rejected" and "executed" are produced. The approval
    columns exist for 11b and stay null until then.
    """
    __tablename__ = "action_records"

    # Origin. decision_run_id is nullable because actions may in future be
    # proposed outside a decision (e.g. manual API trigger). user_id is the
    # caller; proposal source is distinct from execution authorization.
    decision_run_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("decision_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    tool_name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    arguments: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)

    # pending_approval | approved | rejected | executed | failed
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending_approval", index=True
    )

    # structured record of what validation ran and what it said:
    # {"schema": {"passed": bool, "error": str | None},
    #  "permission": {"passed": bool, "error": str | None},
    #  "risk_level": "read", "would_require_approval": bool}
    validation_result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # structured record of execution:
    # {"output": {...}, "error": str | None, "duration_ms": int}
    execution_result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Reserved for 11b.
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    proposed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Post-action verification. In 11b, verify() runs after a successful
    # execute(); the outcome is recorded here. A verification failure does
    # NOT roll back the side effect — Sansa reports "executed but not
    # verified" so the record reflects reality.
    verification_result: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    verification_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )