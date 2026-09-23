# app/db/models/analytics.py
from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class KPIDefinition(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    A named metric computed from source data by deterministic code.

    Global (not tenant-scoped). The formula is a JSON tree evaluated by
    app/services/analytics/evaluator.py — never by an LLM, never by
    arbitrary SQL. The fixed operation set lives in
    app/services/analytics/operations.py.

    Two rules the blueprint is explicit about:
      1. KPIs are computations, not concepts. 'Revenue' is a concept;
         'Gross Margin %' is a KPI.
      2. Every KPI's value must be reproducible from its formula + the
         source data. No hidden state, no per-tenant tweaks (until
         TenantKPIOverride lands, deferred).

    Fields:
      key:           stable identifier, e.g. "procurement.total_spend"
      domain:        grouping for the UI — "Procurement", "Finance", etc.
      unit:          "currency" | "percent" | "count" | "ratio" | "days"
                     Purely presentational; the engine always computes a
                     raw number.
      value_type:    "number" | "integer"
      formula:       JSON tree, shape documented in operations.py
      owner_pack:    pack_key that introduced this KPI, if any. Null means
                     it came from the base catalog.
    """
    __tablename__ = "kpi_definitions"

    key: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    domain: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    unit: Mapped[str | None] = mapped_column(String(20), nullable=True)
    value_type: Mapped[str] = mapped_column(String(20), nullable=False, default="number")

    formula: Mapped[dict] = mapped_column(JSONB, nullable=False)

    owner_pack: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)