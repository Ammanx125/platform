# app/db/models/analytics.py
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


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

class AnomalyDetectorDefinition(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    A named detector configuration.

    Global (not tenant-scoped). Detectors describe WHAT to look at
    (value concept, grouping concept, time basis) and HOW (which detector
    algorithm, parameters, severity on fire).

    detector:
      - 'zscore_global'      — |x - mean| > k * std
      - 'zscore_rolling'     — |x - rolling_mean(w)| > k * rolling_std(w)
      - 'iqr'                — x outside [Q1 - k*IQR, Q3 + k*IQR]
      - 'isolation_forest'   — multivariate score; needs >= 50 points

    parameters: detector-specific. See app/services/analytics/detectors.py
      zscore_global / zscore_rolling:  {"k": 3.0, "window": 7 (rolling only)}
      iqr:                             {"k": 1.5}
      isolation_forest:                {"n_estimators": 100, "contamination": "auto"}

    time_basis_preference: ordered list of RowTimestamp kinds to try.
      The series builder uses the first basis present for a given row.
      Default: ["content", "stream", "file", "ingestion"]
    """
    __tablename__ = "anomaly_detector_definitions"

    key: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    domain: Mapped[str] = mapped_column(String(60), nullable=False, index=True)

    value_concept: Mapped[str] = mapped_column(String(120), nullable=False)
    group_by_concept: Mapped[str | None] = mapped_column(String(120), nullable=True)

    detector: Mapped[str] = mapped_column(String(30), nullable=False)
    parameters: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    time_basis_preference: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=lambda: ["content", "stream", "file", "ingestion"],
    )

    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="warning")
    is_enabled: Mapped[bool] = mapped_column(nullable=False, default=True)

    owner_pack: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)


class Anomaly(Base, UUIDPrimaryKeyMixin, TimestampMixin, TenantMixin):
    """
    One detected anomalous point for one detector on one series group.

    expected_min / expected_max are None when the detector doesn't produce
    a range (isolation_forest). score is always populated.

    severity mirrors the detector's configured severity at detection time.
    Persisted per-row so that changing the detector's severity later does
    not retroactively alter historical anomalies.
    """
    __tablename__ = "anomalies"

    detector_key: Mapped[str] = mapped_column(
        String(120),
        ForeignKey("anomaly_detector_definitions.key", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    source_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    group_key: Mapped[str | None] = mapped_column(String(500), nullable=True, index=True)
    group_label: Mapped[str | None] = mapped_column(String(500), nullable=True)

    time_basis_used: Mapped[str] = mapped_column(String(20), nullable=False)

    point_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    value: Mapped[float] = mapped_column(Float, nullable=False)

    expected_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    score: Mapped[float] = mapped_column(Float, nullable=False)

    severity: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Free-form detail: the point's neighbours, the detector's fitted stats, etc.
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint(
            "detector_key", "tenant_id", "group_key", "point_timestamp",
            name="uq_anomalies_identity",
        ),
        Index("ix_anomalies_tenant_detected", "tenant_id", "detected_at"),
        Index("ix_anomalies_tenant_severity", "tenant_id", "severity"),
    )

class Forecast(Base, UUIDPrimaryKeyMixin, TimestampMixin, TenantMixin):
    """
    One persisted forecast run.

    Not idempotent — running the same query twice produces two rows. The
    dashboard reads the most recent by trained_at; historical runs stay
    for trend-in-forecasts analysis.

    predicted_points / lower_bound / upper_bound are parallel JSONB lists
    of {timestamp, value} objects. Bounds may be empty for models that
    don't produce them (naive, seasonal_naive, moving_average).

    reliability: 'high' | 'medium' | 'low' | 'none'
      'none' is set only when forecasting was refused (insufficient history).

    status: 'ok' | 'insufficient_history' | 'failed'
    """
    __tablename__ = "forecasts"

    value_concept: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    group_key: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    group_label: Mapped[str] = mapped_column(String(500), nullable=False, default="")

    source_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    horizon: Mapped[int] = mapped_column(nullable=False)
    frequency: Mapped[str] = mapped_column(String(20), nullable=False, default="irregular")
    has_seasonality: Mapped[bool] = mapped_column(nullable=False, default=False)
    seasonality_period: Mapped[int | None] = mapped_column(nullable=True)

    model_used: Mapped[str | None] = mapped_column(String(40), nullable=True)
    candidates_evaluated: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    evaluation_metric: Mapped[str] = mapped_column(String(20), nullable=False, default="mae")
    validation_error: Mapped[float | None] = mapped_column(Float, nullable=True)

    predicted_points: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    lower_bound: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    upper_bound: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    reliability: Mapped[str] = mapped_column(String(20), nullable=False, default="none")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="ok")

    notes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    trained_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        Index("ix_forecasts_tenant_concept", "tenant_id", "value_concept"),
        Index("ix_forecasts_tenant_trained", "tenant_id", "trained_at"),
    )