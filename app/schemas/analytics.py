# app/schemas/analytics.py
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AnomalyDetectorRead(BaseModel):
    key: str
    display_name: str
    description: str | None
    domain: str
    value_concept: str
    group_by_concept: str | None
    detector: str
    parameters: dict[str, Any]
    time_basis_preference: list[str]
    severity: str
    is_enabled: bool

    model_config = {"from_attributes": True}


class AnomalyRead(BaseModel):
    id: uuid.UUID
    detector_key: str
    source_ids: list[str]
    group_key: str | None
    group_label: str | None
    time_basis_used: str
    point_timestamp: datetime
    value: float
    expected_min: float | None
    expected_max: float | None
    score: float
    severity: str
    detected_at: datetime
    detail: dict[str, Any]

    model_config = {"from_attributes": True}


class RunDetectorRequest(BaseModel):
    source_ids: list[uuid.UUID] | None = None


class AdHocDetectRequest(BaseModel):
    value_concept: str
    group_by_concept: str | None = None
    detector: str = "zscore_global"
    parameters: dict[str, Any] = {}
    time_basis_preference: list[str] = ["content", "stream", "file", "ingestion"]
    source_ids: list[uuid.UUID] | None = None
    severity: str = "info"


class KPIDefinitionRead(BaseModel):
    key: str
    display_name: str
    description: str | None
    domain: str
    unit: str | None
    value_type: str
    formula: dict[str, Any]

    model_config = {"from_attributes": True}


class KPIResultRead(BaseModel):
    key: str
    display_name: str
    domain: str
    unit: str | None
    value_type: str
    value: float | dict[str, float] | None
    formula_trace: dict[str, Any]
    source_ids: list[uuid.UUID]
    rows_considered: int
    computed_at: datetime
    error: str | None


class KPIEvaluateRequest(BaseModel):
    source_ids: list[uuid.UUID] | None = Field(
        default=None,
        description="Restrict to these sources. None means all tenant sources.",
    )

class ForecastRead(BaseModel):
    id: uuid.UUID
    value_concept: str
    group_key: str
    group_label: str
    source_ids: list[str]
    horizon: int
    frequency: str
    has_seasonality: bool
    seasonality_period: int | None
    model_used: str | None
    candidates_evaluated: list[dict[str, Any]]
    evaluation_metric: str
    validation_error: float | None
    predicted_points: list[dict[str, Any]]
    lower_bound: list[dict[str, Any]]
    upper_bound: list[dict[str, Any]]
    reliability: str
    status: str
    notes: dict[str, Any]
    trained_at: datetime

    model_config = {"from_attributes": True}


class ForecastRunRequest(BaseModel):
    value_concept: str
    group_by_concept: str | None = None
    horizon: int | None = None
    source_ids: list[uuid.UUID] | None = None
    models: list[str] | None = None       # override candidate set