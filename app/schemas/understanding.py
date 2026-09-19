# app/schemas/understanding.py
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class DataProfileRead(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID
    source_id: uuid.UUID
    profiled_at: datetime
    row_count: int
    column_count: int
    profile: dict[str, Any]
    issues: list[dict[str, Any]]

    model_config = {"from_attributes": True}