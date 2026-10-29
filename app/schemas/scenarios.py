# app/schemas/scenarios.py
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.services.scenarios.engine import (
    SUPPLIER_PRICE_CHANGE_MAX,
    SUPPLIER_PRICE_CHANGE_MIN,
)

ScenarioType = Literal["supplier_spend_sensitivity_v1"]


class SupplierSpendAssumptions(BaseModel):
    """
    The user's what-if inputs.

    Bounds are policy from the release decision, not universal business
    rules: keeping a demo scenario inside a believable range stops the
    results from reading like a gimmick. ``extra="forbid"`` means a typo
    in the client is a 422, not a silently ignored assumption.
    """

    supplier_price_change_pct: Decimal = Field(
        ge=SUPPLIER_PRICE_CHANGE_MIN,
        le=SUPPLIER_PRICE_CHANGE_MAX,
        description=(
            "Uniform change applied to every supplier's price. "
            f"Allowed range {SUPPLIER_PRICE_CHANGE_MIN} to "
            f"{SUPPLIER_PRICE_CHANGE_MAX} percent."
        ),
    )

    model_config = {"extra": "forbid"}

    @field_validator("supplier_price_change_pct")
    @classmethod
    def _finite(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("supplier_price_change_pct must be a finite number")
        return value


class ScenarioCalculateRequest(BaseModel):
    scenario_type: ScenarioType = "supplier_spend_sensitivity_v1"
    source_id: uuid.UUID
    assumptions: SupplierSpendAssumptions

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


# ---------- read models ----------


class SupplierProjectionRead(BaseModel):
    supplier_key: str
    baseline_spend: Decimal
    projected_spend: Decimal
    increase: Decimal
    share_of_total_increase_pct: Decimal | None


class SpendTotalsRead(BaseModel):
    baseline_spend: Decimal
    projected_spend: Decimal
    increase: Decimal
    increase_pct: Decimal | None


class ScenarioResultRead(BaseModel):
    scenario_type: str
    formula_version: int
    price_change_pct: Decimal
    baseline: SpendTotalsRead
    scenario: SpendTotalsRead
    suppliers: list[SupplierProjectionRead]
    explanation: str
    limitations: list[str]


class ScenarioProvenanceRead(BaseModel):
    source_id: uuid.UUID
    source_name: str
    snapshot_mode: str
    ingestion_job_ids: list[uuid.UUID]
    spend_shape: str
    spend_columns: dict[str, str]
    supplier_concept_column: str
    rows_considered: int
    rows_skipped: int
    skipped_examples: list[str]
    latest_ingested_at: datetime | None


class ScenarioPreviewResponse(BaseModel):
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
    result: ScenarioResultRead
    provenance: ScenarioProvenanceRead
    is_stale: bool
    stale_reason: str | None
    created_by_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class ScenarioSummaryRead(BaseModel):
    id: uuid.UUID
    name: str
    scenario_type: str
    source_id: uuid.UUID
    status: str
    baseline_spend: Decimal
    projected_spend: Decimal
    increase: Decimal
    is_stale: bool
    created_at: datetime


class SourceCapabilityRead(BaseModel):
    """
    Whether one source can be simulated, and if not, exactly what is
    missing. The UI uses this to enable or explain instead of offering a
    slider that would fail on submit.
    """

    source_id: uuid.UUID
    source_name: str
    source_type: str
    snapshot_mode: str
    supported: bool
    code: str
    detail: str
    missing_concepts: list[str] = Field(default_factory=list)
    supplier_count: int | None = None
    total_spend: Decimal | None = None
    rows_considered: int | None = None
    rows_skipped: int | None = None


class ScenarioCapabilitiesRead(BaseModel):
    scenario_type: str
    formula_version: int
    price_change_bounds: dict[str, Decimal]
    supported: bool
    sources: list[SourceCapabilityRead]
    limitations: list[str]
