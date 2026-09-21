# app/db/models/semantic.py
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class CanonicalConcept(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    A business meaning that source data can be mapped to.

    Global (not tenant-scoped). Industry packs extend this catalog with new
    concepts; they never redefine existing ones.

    kind:
      - 'attribute': a single value extracted from one source column
      - 'entity':    an identifier or name referring to a business object
      - 'fact':      a business event/record whose instance spans several
                     coordinated columns on one source (mapped by grouping
                     multiple SemanticMapping rows under the same concept)

    value_type:
      - 'string' | 'number' | 'date' | 'boolean' | 'entity'

    Key format: '<Domain>.<Name>' — e.g. 'Procurement.Supplier',
    'Finance.Revenue'. Convention only; not parsed.
    """
    __tablename__ = "canonical_concepts"

    key: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    domain: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    value_type: Mapped[str] = mapped_column(String(20), nullable=False)
    synonyms: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)


class SemanticMapping(Base, UUIDPrimaryKeyMixin, TimestampMixin, TenantMixin):
    """
    One source column mapped to one canonical concept, for one source.

    A fact concept is recognized by the existence of multiple rows with the
    same (source_id, canonical_concept_key) — one per participating column.

    status:
      - 'proposed': set by the matcher, awaiting human confirmation
      - 'confirmed': accepted by a human (or human-authored)
      - 'rejected': explicitly dismissed

    confidence:
      - 1.0 for exact-name matches
      - < 1.0 for fuzzy matches (rapidfuzz ratio / 100)
      - None for manual (human-authored) mappings

    rationale: structured record of *why* the matcher chose this concept.
      Example: {"method": "exact", "matched": "supplier", "score": 100.0}
    """
    __tablename__ = "semantic_mappings"

    source_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("data_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    source_column: Mapped[str] = mapped_column(String(200), nullable=False)
    canonical_concept_key: Mapped[str] = mapped_column(
        String(120),
        ForeignKey("canonical_concepts.key", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="proposed", index=True
    )
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    rationale: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    confirmed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "source_id", "source_column",
            name="uq_semantic_mappings_source_column",
        ),
    )