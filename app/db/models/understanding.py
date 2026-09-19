# app/db/models/understanding.py
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class DataProfile(Base, UUIDPrimaryKeyMixin, TimestampMixin, TenantMixin):
    """
    Deterministic profile of one ingestion job's staged rows.

    One row per job. Recomputable: run the profiler again and this row is
    replaced. Kept as its own table (not a column on IngestionJob) because
    the profile is large-ish JSONB and may be versioned later.

    profile JSONB shape:
      {
        "row_count": int,
        "column_count": int,
        "duplicate_row_count": int,
        "columns": [
          {
            "name": str,
            "inferred_type": "integer"|"float"|"string"|"date"|"boolean"|"mixed"|"empty",
            "null_count": int,
            "null_pct": float,
            "distinct_count": int,
            "distinct_pct": float,
            "min": any, "max": any,
            "mean": float | None,
            "std": float | None,
            "top_values": [{"value": any, "count": int}, ...],
            "sample_values": [any, ...]
          }, ...
        ],
        "candidate_keys": [
          {"columns": ["a"], "uniqueness": 1.0, "distinct_count": int}
        ]
      }

    issues JSONB shape:
      [
        {
          "code": "invalid_date"|"impossible_quantity"|"duplicate_entity"|
                  "inconsistent_identifier"|"mixed_types"|"high_missingness",
          "severity": "info"|"warning"|"error",
          "column": str | None,
          "detail": str
        }, ...
      ]
    """
    __tablename__ = "data_profiles"

    job_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("ingestion_jobs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("data_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    profiled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    column_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    profile: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    issues: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)