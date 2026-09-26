# app/db/models/decision.py
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class DecisionRun(Base, UUIDPrimaryKeyMixin, TimestampMixin, TenantMixin):
    """
    One execution of the orchestrator for one user question.

    This is the audit record for a decision (blueprint section 25): it
    carries the intent, the plan, the evidence package, the LLM's
    structured output, and validation results. Chain-of-thought is never
    stored; the trace carries what was *done*, not what the model *thought*.

    error is set when the run failed at any stage. A run can have an error
    and still carry a partial response (e.g. LLM failed after evidence was
    assembled); callers inspect both fields.
    """
    __tablename__ = "decision_runs"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    query: Mapped[str] = mapped_column(Text, nullable=False)
    domain_hint: Mapped[str | None] = mapped_column(String(60), nullable=True)

    source_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    intent: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    plan: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    llm_provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    llm_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    llm_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_claims: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    llm_recommended_actions: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list
    )
    llm_tool_calls: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # Claims that survived validation against the evidence package.
    # Tool calls are passed through unvalidated in Step 10; Step 11 will
    # gate them and record the outcome here as well.
    validated_claims: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    dropped_claims: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )