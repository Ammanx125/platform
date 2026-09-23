# app/schemas/analytics.py
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


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