# app/db/models/scenario.py
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class WhatIfScenario(Base, UUIDPrimaryKeyMixin, TimestampMixin, TenantMixin):
    """
    One saved what-if scenario and the numbers it produced.

    Why the baseline is frozen here and not recomputed on every read:
    source data changes as ingestion runs. A manager who saved a scenario
    last week must see the numbers that were true when they saved it, not
    silently different ones. ``baseline_snapshot`` and ``result`` are the
    calculation-time record; ``provenance`` is what makes those numbers
    reproducible and auditable (source, snapshot mode, ingestion jobs,
    confirmed columns, skipped rows).

    Status:
      - 'calculated'  - saved together with a successful calculation
      - 'stale'       - the source's baseline has since changed. The
                        stored numbers are untouched; recalculating is an
                        explicit user action.

    Deliberately NOT stored: the raw business rows. Only the totals,
    supplier breakdown, and the evidence references needed to redo the
    calculation live here.
    """

    __tablename__ = "whatif_scenarios"

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    scenario_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("data_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="calculated", index=True
    )
    assumptions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    baseline_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    stale_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (
        Index("ix_whatif_scenarios_tenant_created", "tenant_id", "created_at"),
        Index("ix_whatif_scenarios_tenant_source", "tenant_id", "source_id"),
    )
