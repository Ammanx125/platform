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



class ConceptRelationship(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    A directional edge between two CanonicalConcepts.

    Global (not tenant-scoped), like the concepts themselves.

    kind:
      - 'belongs_to': from is a child of to (Purchase belongs_to Supplier)
      - 'has_many':   inverse of belongs_to (Supplier has_many Purchase)
      - 'references': soft link, no ownership (Invoice references PurchaseOrder)
      - 'is_a':       taxonomy (PurchaseOrder is_a Document)

    cardinality:
      - '1:1' | '1:N' | 'N:M'

    join_hint:
      How to join the two concepts in SQL when they're materialized as
      tables. Example for Purchase -> Supplier:
        {"left_column": "supplier_id", "right_column": "id"}
      Optional — concepts without a materialized representation (like
      `Purchase` as a fact, spanning rows) leave it null. Shape is
      documented, not enforced.

    The pair (from_key, to_key, kind) is unique — the same edge can't be
    declared twice with the same meaning.
    """
    __tablename__ = "concept_relationships"

    from_key: Mapped[str] = mapped_column(
        String(120),
        ForeignKey("canonical_concepts.key", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    to_key: Mapped[str] = mapped_column(
        String(120),
        ForeignKey("canonical_concepts.key", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    cardinality: Mapped[str] = mapped_column(String(10), nullable=False, default="1:N")

    join_hint: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "from_key", "to_key", "kind",
            name="uq_concept_relationships_edge",
        ),
    )


class IndustryPack(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    A named bundle of concepts, relationships, and (later) KPI definitions.

    Global (not tenant-scoped). Packs are installed per-tenant via
    TenantIndustryPack. Installing a pack materializes its inline concept
    and relationship definitions into the global catalog.

    Structure:
      key:           stable identifier, e.g. "procurement.v1"
      concepts:      list of concept dicts, same shape as seed_concepts.py
      relationships: list of {from_key, to_key, kind, cardinality, join_hint}
      kpi_stubs:     reserved for Step 8 — list of {key, display_name, formula}
      version:       semver-ish string for change tracking

    Installing an already-installed pack re-runs materialization
    idempotently: existing concepts/relationships are updated, new ones
    inserted, and nothing is deleted.
    """
    __tablename__ = "industry_packs"

    key: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[str] = mapped_column(String(20), nullable=False, default="1.0.0")

    concepts: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    relationships: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    kpi_stubs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)


class TenantIndustryPack(Base, UUIDPrimaryKeyMixin, TimestampMixin, TenantMixin):
    """
    Records that a tenant has installed a given pack.
    """
    __tablename__ = "tenant_industry_packs"

    pack_key: Mapped[str] = mapped_column(
        String(80),
        ForeignKey("industry_packs.key", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    enabled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "pack_key", name="uq_tenant_industry_packs"),
    )