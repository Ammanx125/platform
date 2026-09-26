# app/schemas/action.py
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class ActionRecordRead(BaseModel):
    id: uuid.UUID
    decision_run_id: uuid.UUID | None
    user_id: uuid.UUID
    tool_name: str
    arguments: dict[str, Any]
    rationale: str | None
    status: str
    validation_result: dict[str, Any]
    execution_result: dict[str, Any]
    error_message: str | None
    rejection_reason: str | None
    proposed_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int

    model_config = {"from_attributes": True}