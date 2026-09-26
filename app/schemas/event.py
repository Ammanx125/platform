# app/schemas/event.py
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class EventRead(BaseModel):
    id: uuid.UUID
    event_type: str
    source_id: uuid.UUID | None
    occurred_at: datetime
    received_at: datetime
    dedup_key: str | None
    payload: dict[str, Any]
    status: str
    processed_at: datetime | None
    error: str | None

    model_config = {"from_attributes": True}