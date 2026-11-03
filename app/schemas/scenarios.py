from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.services.scenarios import engine

ScenarioType = Literal["profit_sensitivity_v1"]


class ProfitAssumptionsRead(BaseModel):
    supplier_cost_change_pct: Decimal
    selling_price_change_pct: Decimal
    volume_change_pct: Decimal


class ProfitAssumptions(ProfitAssumptionsRead):
    supplier_cost_change_pct: Decimal = Field(
        ge=engine.SUPPLIER_COST_CHANGE_MIN,
        le=engine.SUPPLIER_COST_CHANGE_MAX,
    )
    selling_price_change_pct: Decimal = Field(
        ge=engine.SELLING_PRICE_CHANGE_MIN,
        le=engine.SELLING_PRICE_CHANGE_MAX,
    )
    volume_change_pct: Decimal = Field(
        ge=engine.VOLUME_CHANGE_MIN,
        le=engine.VOLUME_CHANGE_MAX,
    )

    model_config = {"extra": "forbid"}

    @field_validator(
        "supplier_cost_change_pct",
        "selling_price_change_pct",
        "volume_change_pct",
    )
    @classmethod
    def _finite(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("assumptions must be finite numbers")
        return value


class ScenarioCalculateRequest(BaseModel):
    scenario_type: ScenarioType = "profit_sensitivity_v1"
    source_id: uuid.UUID
    assumptions: ProfitAssumptions

    model_config = {"extra": "forbid"}


class ScenarioSaveRequest(ScenarioCalculateRequest):
    name: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("name must not be blank")
        return cleaned


class ScenarioCompareRequest(BaseModel):
    scenario_ids: list[uuid.UUID] = Field(min_length=2, max_length=3)

    model_config = {"extra": "forbid"}

    @field_validator("scenario_ids")
    @classmethod
    def _unique_scenarios(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(set(value)) != len(value):
            raise ValueError("scenario_ids must be unique")
        return value


class ProfitMetricsRead(BaseModel):
    revenue: Decimal
    cogs: Decimal
    gross_profit: Decimal
    gross_margin_pct: Decimal | None


class ScenarioDeltaRead(BaseModel):
    gross_profit: Decimal
    gross_profit_pct: Decimal | None
    gross_margin_percentage_points: Decimal | None


class ScenarioResultRead(BaseModel):
    scenario_type: str
    formula_version: int
    currency: str
    assumptions: ProfitAssumptionsRead
    baseline: ProfitMetricsRead
    scenario: ProfitMetricsRead
    delta: ScenarioDeltaRead
    explanation: str
    limitations: list[str]


class ScenarioProvenanceRead(BaseModel):
    source_id: uuid.UUID
    source_name: str
    snapshot_mode: str
    ingestion_job_ids: list[uuid.UUID]
    revenue_concept: str
    revenue_column: str
    cogs_concept: str
    cogs_column: str
    currency_concept: str
    currency_column: str
    currency: str
    rows_considered: int
    rows_skipped: int
    skipped_examples: list[str]
    latest_ingested_at: datetime | None


class ScenarioPreviewResponse(BaseModel):
    status: Literal["calculated"] = "calculated"
    result: ScenarioResultRead
    provenance: ScenarioProvenanceRead
    calculated_at: datetime


class ScenarioRead(BaseModel):
    id: uuid.UUID
    name: str
    scenario_type: str
    source_id: uuid.UUID
    status: str
    assumptions: dict[str, Any]
    result: ScenarioResultRead | None
    provenance: ScenarioProvenanceRead | None
    is_stale: bool
    stale_reason: str | None
    created_by_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    supported: bool
    compatibility_note: str | None


class ScenarioSummaryRead(BaseModel):
    id: uuid.UUID
    name: str
    scenario_type: str
    source_id: uuid.UUID
    status: str
    currency: str | None
    baseline_gross_profit: Decimal | None
    scenario_gross_profit: Decimal | None
    gross_profit_change: Decimal | None
    is_stale: bool
    created_at: datetime
    supported: bool


class ScenarioCompareResponse(BaseModel):
    source_id: uuid.UUID
    currency: str
    scenarios: list[ScenarioRead] = Field(min_length=2, max_length=3)


class SourceCapabilityRead(BaseModel):
    source_id: uuid.UUID
    source_name: str
    source_type: str
    snapshot_mode: str
    supported: bool
    code: str
    detail: str
    missing_concepts: list[str] = Field(default_factory=list)
    currency: str | None = None
    revenue: Decimal | None = None
    cogs: Decimal | None = None
    rows_considered: int | None = None
    rows_skipped: int | None = None


class ScenarioCapabilitiesRead(BaseModel):
    scenario_type: str
    formula_version: int
    assumption_bounds: dict[str, dict[str, Decimal]]
    supported: bool
    sources: list[SourceCapabilityRead]
    limitations: list[str]
