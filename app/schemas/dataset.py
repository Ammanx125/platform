# app/schemas/dataset.py
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class DataSourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    source_type: str = Field(min_length=1, max_length=40)
    config: dict = Field(default_factory=dict)


class WebhookSourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class WebhookSourceCreated(BaseModel):
    id: uuid.UUID
    name: str
    source_type: str
    token: str
    secret: str


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

class HTTPSourceConfig(BaseModel):
    """
    Config for a DataSource with source_type='http'.

    All fields except `url` are optional. `auth_ref` refers to a value
    resolved by app.core.secrets (env var SANSA_SECRET_<auth_ref>).
    """
    url: str
    method: Literal["GET", "POST"] = "GET"
    format: Literal["json", "csv"] = "json"
    headers: dict[str, str] = Field(default_factory=dict)
    json_path: str | None = None       # e.g. "$.data.items"
    auth_ref: str | None = None
    auth_header: str = "Authorization"
    auth_scheme: str | None = None     # e.g. "Bearer"
    timeout_seconds: int = 30
    max_response_bytes: int = 10 * 1024 * 1024
    allowed_hosts: list[str] = Field(default_factory=list)
    body: dict | None = None           # for POST


class SQLSourceConfig(BaseModel):
    """
    Config for a DataSource with source_type='sql'.

    `credential_ref` refers to a value resolved by app.core.secrets
    (env var SANSA_SECRET_<credential_ref>).
    """
    credential_ref: str
    query: str
    row_limit: int = 10000
    timeout_seconds: int = 30
    