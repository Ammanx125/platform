# app/schemas/dataset.py
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class DataSourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    source_type: str = Field(min_length=1, max_length=40)
    config: dict = Field(default_factory=dict)


class DataSourceRead(BaseModel):
    id: uuid.UUID
    name: str
    source_type: str
    is_active: bool
    config: dict
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class IngestionJobRead(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    status: str
    rows_read: int
    rows_staged: int
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class IngestionJobList(BaseModel):
    items: list[IngestionJobRead]


class StagedRowRead(BaseModel):
    id: uuid.UUID
    row_number: int
    raw_data: dict

    model_config = {"from_attributes": True}